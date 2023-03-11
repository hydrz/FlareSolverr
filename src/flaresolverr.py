import json
import logging
import os
import sys
import requests
import urllib3

from bottle import run, response, Bottle, request, LocalRequest

from bottle_plugins.error_plugin import error_plugin
from bottle_plugins.logger_plugin import logger_plugin
from dtos import IndexResponse, V1RequestBase
import flaresolverr_service
import utils


class JSONErrorBottle(Bottle):
    """
    Handle 404 errors
    """
    def default_error_handler(self, res):
        response.content_type = 'application/json'
        return json.dumps(dict(error=res.body, status_code=res.status_code))


app = JSONErrorBottle()

# plugin order is important
app.install(logger_plugin)
app.install(error_plugin)


@app.route('/')
def index():
    """
    Show welcome message
    """
    res = flaresolverr_service.index_endpoint()
    return utils.object_to_dict(res)


@app.route('/health')
def health():
    """
    Healthcheck endpoint.
    This endpoint is special because it doesn't print traces
    """
    res = flaresolverr_service.health_endpoint()
    return utils.object_to_dict(res)


@app.post('/v1')
def controller_v1():
    """
    Controller v1
    """
    req = V1RequestBase(request.json)
    res = flaresolverr_service.controller_v1_endpoint(req)
    if res.__error_500__:
        response.status = 500
    return utils.object_to_dict(res)


endpoint = os.environ.get('Endpoint', 'https://chat.openai.com/backend-api')
maxTimeoutMilliseconds = os.environ.get('MaxTimeoutMilliseconds', 30000)


@app.route('/backend-api/<path>',
           method=['GET', 'POST', 'PATCH', "DELETE", "OPTIONS", "PUT"])
def backend_api(path):
    """
    Reverse proxy endpoint. All requests are forwarded to https://chat.openai.com/backend-api
    """

    url = endpoint + '/' + path
    if request.query_string:
        url = url + '?' + request.query_string

    resp = proxy(url, request)

    if resp.status_code != 403:
        logging.info(f'not need to use flaresolverr')
        response.status = resp.status_code
        for header in resp.headers:
            response.set_header(header, resp.headers[header])
        return resp.content

    req = {}
    req['url'] = url
    req['maxTimeout'] = maxTimeoutMilliseconds
    req['cookies'] = request.cookies

    if request.method == 'GET':
        req['cmd'] = 'request.get'
    elif request.method == 'POST':
        req['cmd'] = 'request.post'
        # convert json data to application/x-www-form-urlencoded
        if request.json is not None:
            postData = urllib3.request.urlencode(request.json)
            req['postData'] = postData
    else:
        return requests.request(request.method, url, data=request.json)

    res = flaresolverr_service.controller_v1_endpoint(V1RequestBase(req))

    if res.__error_500__:
        response.status = 500
        return

    if res.status is None or res.status != 'ok':
        response.status = 400
        return

    if res.solution is None:
        response.status = 400
        return

    response.status = res.solution.status

    for header in res.solution.headers:
        response.set_header(header, res.solution.headers[header])

    return res.solution.response


def proxy(url, req: LocalRequest):
    if req.method == 'GET':
        return requests.get(url, headers=req.headers, cookies=req.cookies)
    elif req.method == 'POST':
        return requests.post(url,
                             headers=req.headers,
                             cookies=req.cookies,
                             data=req.json)
    elif req.method == 'PATCH':
        return requests.patch(url,
                              headers=req.headers,
                              cookies=req.cookies,
                              data=req.json)
    elif req.method == 'DELETE':
        return requests.delete(url,
                               headers=req.headers,
                               cookies=req.cookies,
                               data=req.json)
    elif req.method == 'OPTIONS':
        return requests.options(url, headers=req.headers, cookies=req.cookies)
    elif req.method == 'PUT':
        return requests.put(url,
                            headers=req.headers,
                            cookies=req.cookies,
                            data=req.json)
    else:
        return requests.request(req.method,
                                url,
                                headers=req.headers,
                                cookies=req.cookies,
                                data=req.json)


if __name__ == "__main__":
    # validate configuration
    log_level = os.environ.get('LOG_LEVEL', 'info').upper()
    log_html = utils.get_config_log_html()
    headless = utils.get_config_headless()
    server_host = os.environ.get('HOST', '0.0.0.0')
    server_port = int(os.environ.get('PORT', 8191))

    # configure logger
    logger_format = '%(asctime)s %(levelname)-8s %(message)s'
    if log_level == 'DEBUG':
        logger_format = '%(asctime)s %(levelname)-8s ReqId %(thread)s %(message)s'
    logging.basicConfig(format=logger_format,
                        level=log_level,
                        datefmt='%Y-%m-%d %H:%M:%S',
                        handlers=[logging.StreamHandler(sys.stdout)])
    # disable warning traces from urllib3
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    logging.getLogger('selenium.webdriver.remote.remote_connection').setLevel(
        logging.WARNING)
    logging.getLogger('undetected_chromedriver').setLevel(logging.WARNING)

    logging.info(f'FlareSolverr {utils.get_flaresolverr_version()}')
    logging.debug('Debug log enabled')

    # test browser installation
    flaresolverr_service.test_browser_installation()

    # start webserver
    # default server 'wsgiref' does not support concurrent requests
    run(app, host=server_host, port=server_port, quiet=True, server='waitress')
