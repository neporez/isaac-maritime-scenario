from maritime_scenario.core.events import Signal


def test_connect_and_emit():
  received = []
  sig = Signal()
  sig.connect(lambda *a, **kw: received.append((a, kw)))
  sig.emit(1, 2, key="value")
  assert received == [((1, 2), {"key": "value"})]


def test_disconnect_stops_delivery():
  received = []
  sig = Signal()
  token = sig.connect(lambda: received.append(1))
  sig.emit()
  sig.disconnect(token)
  sig.emit()
  assert received == [1]


def test_one_broken_listener_does_not_block_others():
  received = []

  def bad():
    raise RuntimeError("boom")

  sig = Signal()
  sig.connect(bad)
  sig.connect(lambda: received.append("ok"))
  sig.emit()   # must not raise
  assert received == ["ok"]


def test_multiple_subscribers_all_called():
  received = []
  sig = Signal()
  sig.connect(lambda: received.append("a"))
  sig.connect(lambda: received.append("b"))
  sig.emit()
  assert received == ["a", "b"]
