import logging
import socket
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from mode.utils.futures import done_future
from mode.utils.imports import symbol_by_name
from faust.worker import WEBSITE_CLS, Worker
from faust.utils import terminal


class CoroEq:

    def __init__(self, coro):
        self.coro = coro

    def __eq__(self, other):
        return other.cr_code.co_name == self.coro.__name__


class test_Worker:

    @pytest.fixture
    def worker(self, app):
        return Worker(app, web_port=8080)

    def test_constructor(self, app):
        w = Worker(app)
        assert w.app is app
        assert w.sensors == set()
        assert w.workdir == Path.cwd()
        assert w.Website == symbol_by_name(WEBSITE_CLS)
        assert w.web_port is None
        assert w.web_bind is None
        assert w.web_host == socket.gethostname()
        assert isinstance(w.spinner, terminal.Spinner)

    def test_set_sensors(self, app):
        assert Worker(app, sensors=[1, 2]).sensors == {1, 2}

    def test_set_workdir(self, app):
        assert Worker(app, workdir='/foo').workdir == Path('/foo')

    def test_set_website(self, app):
        assert Worker(app, Website='unittest.mock.Mock').Website is Mock

    def test_set_web_port(self, app):
        assert Worker(app, web_port=8080).web_port == 8080

    def test_set_web_bind(self, app):
        assert Worker(app, web_bind='***').web_bind == '***'

    def test_set_web_host(self, app):
        assert Worker(app, web_host='foo').web_host == 'foo'

    @pytest.mark.asyncio
    async def test__on_siginit(self, worker):
        with patch('asyncio.ensure_future') as ensure_future:
            worker._on_sigint()
            assert worker._shutdown_immediately
            assert worker.spinner.stopped
            ensure_future.assert_called_with(
                CoroEq(worker._stop_on_signal), loop=worker.loop)

    def test__on_siginit__no_spinner(self, worker):
        worker.spinner = None
        with patch('asyncio.ensure_future') as ensure_future:
            worker._on_sigint()

    @pytest.mark.asyncio
    async def test__on_sigterm(self, worker):
        with patch('asyncio.ensure_future') as ensure_future:
            worker._on_sigterm()
            assert worker._shutdown_immediately
            assert worker.spinner.stopped
            ensure_future.assert_called_with(
                CoroEq(worker._stop_on_signal), loop=worker.loop)

    @pytest.mark.asyncio
    async def test_on_startup_finished__shutdown_requested(self, worker):
        worker._shutdown_immediately = True
        worker._on_shutdown_immediately = Mock(name='on_shutdown_immediately')
        await worker.on_startup_finished()
        worker._on_shutdown_immediately.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_on_startup_finished(self, worker):
        worker.maybe_start_blockdetection = Mock(name='maybe_start_block')
        worker.maybe_start_blockdetection.return_value = done_future()
        worker._on_startup_end_spinner= Mock(name='on_startup_end_spinner')
        await worker.on_startup_finished()
        worker.maybe_start_blockdetection.assert_called_once_with()
        worker._on_startup_end_spinner.assert_called_once_with()

    def test_on_startup_end_spinner(self, worker):
        spinner = worker.spinner = Mock(name='spinner')
        spinner.file.isatty.return_value = True
        worker.say = Mock(name='say')
        worker._on_startup_end_spinner()
        spinner.finish.assert_called_once_with()
        assert worker.spinner is None
        worker.say.assert_called_once_with(' 😊')

    def test_on_startup_end_spinner__no_spinner(self, worker):
        worker.spinner = None
        worker.log = Mock(name='log')
        worker._on_startup_end_spinner()
        worker.log.info.assert_called_once_with('Ready')

    def test_on_startup_end_spinner__notatty(self, worker):
        spinner = worker.spinner = Mock(name='spinner')
        spinner.file.isatty.return_value = False
        worker.say = Mock(name='say')
        worker._on_startup_end_spinner()
        spinner.finish.assert_called_once_with()
        assert worker.spinner is None
        worker.say.assert_called_once_with(' OK ^')

    def test_on_shutdown_immediately(self, worker):
        worker.say = Mock(name='say')
        worker._on_shutdown_immediately()
        worker.say.assert_called_once_with('')

    def test_on_init_dependencies(self, worker, app):
        app.beacon = Mock(name='app.beacon')
        deps = worker.on_init_dependencies()
        assert list(deps) == (
            [worker.website] +
            list(worker.services) +
            [app]
        )
        app.beacon.reattach.assert_called_once_with(worker.beacon)
        assert app.on_startup_finished == worker.on_startup_finished

    def test_on_init_dependencies__sensors_to_app(self, worker, app):
        s1 = Mock(name='S1')
        s2 = Mock(name='S2')
        worker.sensors = {s1, s2}
        worker.on_init_dependencies()
        assert app.sensors._sensors.issubset(worker.sensors)

    @pytest.mark.asyncio
    async def test_on_first_start(self, worker):
        worker.change_workdir = Mock(name='change_workdir')
        worker.autodiscover = Mock(name='autodiscover')
        worker.default_on_first_start = Mock(name='default_on_first_start')
        await worker.on_first_start()
        worker.change_workdir.assert_called_once_with(worker.workdir)
        worker.autodiscover.assert_called_once_with()
        worker.default_on_first_start.assert_called_once_with()

    def test_change_workdir(self, worker):
        with patch('os.chdir') as chdir:
            p = Path('baz')
            worker.change_workdir(p)
            chdir.assert_called_once_with(p.absolute())

    def test_change_workdir__already_cwd(self, worker):
        with patch('os.chdir') as chdir:
            p = Path.cwd()
            worker.change_workdir(p)
            chdir.assert_not_called()

    def test_autodiscover(self, worker):
        worker.app.conf.autodiscover = True
        worker.app.discover = Mock(name='discover')
        worker.autodiscover()
        worker.app.discover.assert_called_once_with()

    def test_autodiscover__disabled(self, worker):
        worker.app.conf.autodiscover = False
        worker.app.discover = Mock(name='discover')
        worker.autodiscover()
        worker.app.discover.assert_not_called()

    def test_setproctitle(self, worker, app):
        with patch('faust.worker.setproctitle') as setproctitle:
            worker._setproctitle('foo')
            setproctitle.assert_called_with(
                f'[Faust:Worker] -foo- testid -p 8080 '
                f'{app.conf.datadir.absolute()}')

    def test_proc_ident(self, worker, app):
        assert (worker._proc_ident() ==
                f'testid -p 8080 {app.conf.datadir.absolute()}')

    @pytest.mark.asyncio
    async def test_on_execute(self, worker):
        worker._setproctitle = Mock(name='setproctitle')
        worker.spinner = Mock(name='spinner')
        worker._say = Mock(name='say')
        await worker.on_execute()
        worker._setproctitle.assert_called_with('init')
        worker._say.assert_called_with('starting➢ ', end='')
        worker.spinner = None
        await worker.on_execute()

    def test_on_setup_root_logger(self, worker):
        worker._disable_spinner_if_level_below_WARN = Mock(name='dd')
        worker._setup_spinner_handler = Mock(name='ss')
        logger = Mock(name='logger')
        worker.on_setup_root_logger(logger, logging.INFO)
        worker._disable_spinner_if_level_below_WARN.assert_called_with(
            logging.INFO)
        worker._setup_spinner_handler.assert_called_with(logger, logging.INFO)

    @pytest.mark.parametrize('loglevel,expected', [
        (None, True),
        (logging.CRITICAL, True),
        (logging.ERROR, True),
        (logging.WARN, True),
        (logging.INFO, False),
        (logging.DEBUG, False),
    ])
    def test_disable_spinner_if_level_below_WARN(self, loglevel, expected,
                                                 worker):
        worker._disable_spinner_if_level_below_WARN(loglevel)
        if expected:
            assert worker.spinner
        else:
            assert worker.spinner is None

    def test_setup_spinner_handler(self, worker):
        logger = Mock(name='logger')
        logger.handlers = [Mock(name='handler')]
        with patch('faust.utils.terminal.SpinnerHandler') as SpinnerHandler:
            worker._setup_spinner_handler(logger, logging.INFO)
            logger.handlers[0].setLevel.assert_called_with(logging.INFO)
            SpinnerHandler.assert_called_once_with(
                worker.spinner, level=logging.DEBUG)
            logger.addHandler.assert_called_once_with(SpinnerHandler())
            logger.setLevel.assert_called_once_with(logging.DEBUG)

    def test_setup_spinner_handler__when_no_spinner(self, worker):
        worker.spinner = None
        worker._setup_spinner_handler(Mock(name='logger'), logging.INFO)

    def test_website(self, app):
        with patch('faust.web.site.Website') as Website:
            worker = Worker(app)
            website = worker.website
            Website.assert_called_once_with(
                worker.app,
                bind=worker.web_bind,
                port=worker.web_port,
                loop=worker.loop,
                beacon=worker.beacon,
            )
            assert website is Website()

