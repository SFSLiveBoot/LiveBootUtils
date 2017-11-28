#!/usr/bin/python

from logging import info, warn, error
import logging
import subprocess
from lbu_common import cli_func, BadArgumentsError

logging.getLogger().setLevel(logging.INFO)

_log_colors=dict(map(lambda (k,v):  (k, "\033[%sm"%(v,)),dict(blue="34", red="1;31", yellow="33", reset="1;0").items()))
try: subprocess.check_call(["tty","-s"])
except subprocess.CalledProcessError: pass
else:
    logging.addLevelName(logging.INFO, "{blue}{level}{reset}".format(level=logging.getLevelName(logging.INFO), **_log_colors))
    logging.addLevelName(logging.WARNING, "{yellow}{level}{reset}".format(level=logging.getLevelName(logging.WARNING), **_log_colors))
    logging.addLevelName(logging.ERROR, "{red}{level}{reset}".format(level=logging.getLevelName(logging.ERROR), **_log_colors))

if __name__ == '__main__':
    import sys, os
    args = sys.argv[:]
    arg0=os.path.basename(args.pop(0))
    try: command=args.pop(0)
    except IndexError:
        warn("Usage: %s [{--debug|--quiet}] <command> [<args..>]", arg0)
        info("Supported commands:%s",
             "".join(map(lambda (n, f): "\n\t%s\t%s"%(n, getattr(f, "_cli_desc", "")),
                         sorted(cli_func.commands.iteritems()))))
        raise SystemExit(1)
    if command=='--debug':
        logging.getLogger().setLevel(logging.DEBUG)
        command=args.pop(0)
    elif command=="--quiet":
        logging.getLogger().setLevel(logging.WARN)
        command=args.pop(0)
    logging.getLogger().name=command
    try: cmd_func=cli_func.commands[command]
    except KeyError:
        error("Unknown command: %s", command)
        raise SystemExit(1)
    try: ret=cmd_func.cli_call(args)
    except BadArgumentsError as e:
        error("Execution error: %s", e)
        info("Usage: %s %s %s", arg0, command, cmd_func.__doc__)
        raise SystemExit(1)
    if ret is not None:
        if isinstance(ret, list):
            for e in ret: print e
        elif isinstance(ret, dict):
            print __import__("json").dumps(ret, indent=True)
        else:
            print ret
