import logging
import threading
import time
from types import SimpleNamespace

import pytest

from app.modules.processing.queue_notify import QueueWakeListener, connection_options


def test_connection_url_and_sqlite_rejection():
    options=connection_options('postgresql+psycopg://worker:secret@localhost:5432/cam')
    assert options['user']=='worker'
    assert options['password']=='secret'
    assert options['dbname']=='cam'
    with pytest.raises(ValueError):
        connection_options('sqlite:///:memory:')


class FakeConnection:
    def __init__(self, messages):
        self.messages=messages
        self.commands=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql):self.commands.append(sql)
    def notifies(self,timeout):
        for payload in self.messages:
            yield SimpleNamespace(payload=payload)
        time.sleep(0.01)


def test_job_type_filter_and_global_wakeup():
    wake=threading.Event()
    connection=FakeConnection(['video_analyze','asset_store','*'])
    listener=QueueWakeListener('postgresql://localhost/cam',
        accepted_job_types=('asset_store',),wake_event=wake,
        logger=logging.getLogger('test.notify'),
        connect=lambda **_:connection,reconnect_delay_seconds=0.01)
    listener.start()
    assert wake.wait(1)
    time.sleep(0.05)
    listener.stop()
    assert listener.received_events>=2
    assert 'LISTEN cam_processing_queue_wakeup' in connection.commands


def test_unavailable_listener_does_not_raise_and_stops():
    wake=threading.Event()
    def broken(**kwargs):raise ConnectionError('offline')
    listener=QueueWakeListener('postgresql://localhost/cam',
        accepted_job_types=(),wake_event=wake,
        logger=logging.getLogger('test.notify'),connect=broken,
        reconnect_delay_seconds=0.01)
    listener.start()
    time.sleep(0.06)
    assert listener.reconnections>=1
    listener.stop()
