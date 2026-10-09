"""Tests for the wjs_stats view that shows the django-q Redis queue."""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone
from django_q.brokers import get_broker
from django_q.signing import SignedPackage
from plugins.wjs_stats import views

# These tests need a real Redis server, which is not available in GitLab CI: run them on demand only.
# --run-academic is registered in wjs_review's conftest, which pytest knows only if wjs_review's tests are
# among the given paths (hence the default). From janeway/src:
#   pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests \
#     ../../wjs-profile-project/wjs/jcom_profile/tests/test_stats_qcluster_queue.py --run-academic -k qcluster
pytestmark = pytest.mark.skipif(
    "not config.getoption('--run-academic', default=False)",
    reason="needs a Redis server",
)


@pytest.fixture
def test_broker(monkeypatch):
    """Return a Redis broker on a throw-away list, used by the view instead of the real queue."""
    broker = get_broker(list_key=f"test-{uuid.uuid4().hex}")
    monkeypatch.setattr(views, "get_broker", lambda: broker)
    yield broker
    broker.delete_queue()


def _enqueue(broker, func, *args, **kwargs):
    """Push a task on the broker as async_task would, but without running it (Q_CLUSTER is sync)."""
    task = {
        "id": uuid.uuid4().hex,
        "name": f"task-{func}",
        "func": func,
        "args": args,
        "kwargs": kwargs,
        "started": timezone.now(),
    }
    broker.enqueue(SignedPackage.dumps(task))
    return task


def test_get_queued_tasks_decodes_without_consuming(test_broker):
    """Queued tasks are decoded in queue order and stay in the queue."""
    first = _enqueue(test_broker, "math.floor", 1.5)
    second = _enqueue(test_broker, "math.ceil", 2.5, foo="bar")

    tasks = views.get_queued_tasks(test_broker)

    assert [t["id"] for t in tasks] == [first["id"], second["id"]], "Tasks must be listed in queue order"
    assert tasks[1]["kwargs"] == {"foo": "bar"}, "Task kwargs must be decoded"
    assert test_broker.queue_size() == 2, "Listing the queue must not consume it"


def test_get_queued_tasks_reports_undecodable_task(test_broker):
    """A task that cannot be decoded is reported with an error instead of raising."""
    test_broker.enqueue("not a signed package")
    _enqueue(test_broker, "math.floor", 1.5)

    tasks = views.get_queued_tasks(test_broker)

    assert "error" in tasks[0], "An undecodable task must carry an error"
    assert tasks[1]["func"] == "math.floor", "Tasks after an undecodable one must still be decoded"


def test_get_queued_tasks_respects_limit(test_broker):
    """Only the first ``limit`` tasks are decoded."""
    for i in range(3):
        _enqueue(test_broker, "math.floor", i)

    tasks = views.get_queued_tasks(test_broker, limit=2)

    assert len(tasks) == 2, "No more than ``limit`` tasks must be returned"


@pytest.mark.django_db
def test_qcluster_queue_view_rejects_non_staff(client, journal, coauthor, test_broker):
    """A logged-in user who is not staff cannot see the queue."""
    client.force_login(coauthor)
    response = client.get(reverse("wjs_stats_qcluster_queue"))

    assert response.status_code == 403, "Non-staff users must be forbidden"


@pytest.mark.django_db
def test_qcluster_queue_view_shows_tasks(client, journal, admin, test_broker):
    """Staff can see the queued tasks."""
    _enqueue(test_broker, "plugins.wjs_review.some_module.some_function", 42)
    client.force_login(admin)

    response = client.get(reverse("wjs_stats_qcluster_queue"))

    assert response.status_code == 200, "Staff must be able to see the queue"
    assert response.context["queue_size"] == 1, "The queue length must be in the context"
    assert "plugins.wjs_review.some_module.some_function" in response.content.decode(), "Task function must be shown"
