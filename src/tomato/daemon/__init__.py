"""
**tomato.daemon**: module of functions comprising the tomato daemon
-------------------------------------------------------------------
.. codeauthor::
    Peter Kraus

"""

import logging
import argparse
from pathlib import Path
from threading import Thread
import toml
import time
import zmq

from tomato.models import Reply, Daemon
import tomato.daemon.cmd as cmd
import tomato.daemon.job
import tomato.daemon.driver
import tomato.daemon.io as io

logger = logging.getLogger(__name__)


def setup_logging(daemon: Daemon):
    """
    Helper function to set up logging (folder, filename, verbosity, format) based on
    the passed daemon state.
    """
    logger = logging.getLogger(__name__)
    for handler in logger.handlers:
        logger.removeHandler(handler)
    logdir = Path(daemon.logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(logdir / f"daemon_{daemon.port}.log")
    fh.setLevel(daemon.verbosity)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)8s - %(name)-30s - %(message)s"
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)


def run_daemon():
    """
    The function called when `tomato-daemon` is executed.

    Manages the state of the tomato daemon, including recovery of state via
    :mod:`~tomato.daemon.io`, processing state updates via :mod:`~tomato.daemon.cmd`,
    and the manager threads for both jobs (:mod:`~tomato.daemon.job`) and drivers
    (:mod:`~tomato.daemon.driver`).
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", "-p", type=int, default=1234)
    parser.add_argument("--verbosity", "-V", type=int, default=logging.INFO)
    parser.add_argument("--appdir", "-A", type=Path, default=Path.cwd())
    parser.add_argument("--logdir", "-L", type=Path, default=Path.cwd())
    args = parser.parse_args()
    settings = toml.load(args.appdir / "settings.toml")

    daemon = Daemon(**vars(args), status="bootstrap", settings=settings)
    setup_logging(daemon)
    logger.info(f"logging set up with verbosity {daemon.verbosity}")

    logger.debug("attempting to restore daemon state")
    io.load(daemon)
    logger.debug(f"{daemon=}")

    context = zmq.Context()
    rep = context.socket(zmq.REP)
    logger.debug(f"binding zmq.REP socket on port {daemon.port}")
    rep.bind(f"tcp://127.0.0.1:{daemon.port}")
    poller = zmq.Poller()
    poller.register(rep, zmq.POLLIN)

    logger.debug("entering main loop")
    jmgr = Thread(target=tomato.daemon.job.manager, args=(daemon.port,))
    jmgr.do_run = True
    jmgr.start()
    dmgr = Thread(target=tomato.daemon.driver.manager, args=(daemon.port,))
    dmgr.do_run = True
    dmgr.start()
    t0 = time.process_time()
    while True:
        socks = dict(poller.poll(100))
        if rep in socks:
            msg = rep.recv_pyobj()
            logger.debug(f"received {msg=}")
            if "cmd" not in msg:
                logger.error(f"received msg without cmd: {msg=}")
                ret = Reply(success=False, msg="received msg without cmd", data=msg)
            elif hasattr(cmd, msg["cmd"]):
                ret = getattr(cmd, msg["cmd"])(msg, daemon)
            logger.debug(f"reply with {ret=}")
            rep.send_pyobj(ret)
        if daemon.status == "stop":
            for mgr, label in [(jmgr, "job"), (dmgr, "driver")]:
                if mgr is not None and mgr.do_run:
                    logger.debug(f"stopping {label} manager thread")
                    mgr.do_run = False
            if jmgr is not None:
                jmgr.join(1e-3)
                if not jmgr.is_alive():
                    jmgr = None
                    logger.info("job manager thread joined")
            if dmgr is not None:
                dmgr.join(1e-3)
                if not dmgr.is_alive():
                    dmgr = None
                    logger.info("driver manager thread joined")
            if jmgr is None and dmgr is None:
                io.store(daemon)
                break
        tN = time.process_time()
        if tN - t0 > 10:
            io.store(daemon)
            t0 = tN
    logger.critical(f"tomato-daemon on port {daemon.port} exiting")
