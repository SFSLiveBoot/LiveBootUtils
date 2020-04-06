#!/usr/bin/python

import os
import bottle
import inspect

from bottle import response, abort

from lbu_common import cli_func, BadArgumentsError

app = bottle.Bottle()


@app.get("/")
def index():
    return '<a href="cmd">API</a>'


def allow_origin(func):
    def decor(*args, **kwargs):
        allow_origin = os.environ.get("HTTP_ALLOW_ORIGIN")
        if allow_origin:
            response.add_header("Access-Control-Allow-Origin", allow_origin)
            response.add_header("Vary", "Origin")
            response.add_header("Access-Control-Allow-Methods", "*")
        return func(*args, **kwargs)
    return decor


def func_args_schema(func):
    schema = dict(type="object")
    properties = {}
    required = []
    spec = inspect.getargspec(func)
    rev_args = list(reversed(spec.args))
    defaults = dict(map(lambda (i, d): (rev_args[i], d), enumerate(
        reversed(spec.defaults)))) if spec.defaults else {}
    for n in spec.args:
        properties[n] = dict(type="boolean" if n in defaults and isinstance(
            defaults[n], bool) else "integer" if n in defaults and isinstance(
            defaults[n], int) else "string")
        if n in defaults:
            properties[n]["default"] = defaults[n]
        else:
            required.append(n)
    if spec.varargs:
        properties[spec.varargs] = dict(
            type="array", items=dict(type="string"))
    if spec.keywords:
        properties[spec.keywords] = dict(type="object")
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = required
    return schema


@app.post("/cmd/:cmd")
@allow_origin
def run_command(cmd):
    return abort(501, "TBD: Not implemented yet.")


@app.get("/cmd")
@allow_origin
def list_commands():
    return dict(openapi="3.0.2", info=dict(title="LiveBootUtils REST API", version="0.1"),
                paths=dict(map(lambda (n, f): ("/cmd/%s" % (n), dict(post=dict(
                    summary=f._cli_desc,
                    requestBody=dict(
                        required=True,
                        description=f.__doc__.replace("&", "&amp;").replace(
                            "<", "&lt;").replace(">", "&gt;"),
                        content={
                            "application/x-www-form-urlencoded":
                            dict(schema=func_args_schema(f))}),
                    responses={"200": dict(description="OK")},
                ))), cli_func.commands.iteritems())))


if __name__ == "__main__":
    bottle.run(app, debug=os.environ.get("HTTP_DEBUG"), reloader=bool(os.environ.get("HTTP_RELOAD")),
               port=int(os.environ.get("HTTP_PORT", "8080")), host=os.environ.get("HTTP_HOST", "127.0.0.1"))
