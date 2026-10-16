"""User schedules execute saved flow revisions in an isolated PostgreSQL server."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from test_console import PROJECT_ROOT, _assert_no_horizontal_overflow
from test_flow_builder import _new_flow, _save, _select

pytestmark = [pytest.mark.postgres, pytest.mark.browser]


@pytest.fixture
def istanbul_page(page, console_url):
    context = page.context.browser.new_context(
        viewport={"width": 1440, "height": 1000},
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        reduced_motion="reduce",
    )
    errors, forbidden = [], []

    def restrict_network(route):
        if route.request.url.startswith(console_url + "/"):
            route.continue_()
        else:
            forbidden.append(route.request.resource_type)
            route.abort()

    context.route("**/*", restrict_network)
    localized = context.new_page()
    localized.set_default_timeout(10_000)
    localized.on("pageerror", lambda error: errors.append(str(error)))
    localized.on(
        "console",
        lambda message: (
            errors.append(message.text)
            if message.type == "error" and "content security policy" in message.text.lower()
            else None
        ),
    )
    localized.goto(console_url)
    try:
        yield localized
    finally:
        context.close()
        assert not errors, f"Browser JavaScript/CSP errors: {errors}"
        assert not forbidden, "Browser attempted to contact an external resource."


def _flow(page):
    _new_flow(page, "condition")
    page.locator("#flow-name").fill(f"Zamanlanan akış {uuid4().hex[:10]}")
    _select(page, "yes")
    page.locator("#flow-value").fill("Zamanlanan ilk sürüm")
    return _save(page, 1)


def _schedule(page, *, kind="once", local_time="2040-02-03T11:45"):
    from playwright.sync_api import expect

    page.locator('[data-flow-action="schedule"]').click()
    form = page.locator("#schedule-form")
    expect(form).to_be_visible()
    name = f"Tarayıcı zamanlaması {uuid4().hex[:10]}"
    form.locator('[name="name"]').fill(name)
    form.locator('[name="kind"]').select_option(kind)
    form.locator('[name="next_run_at"]').fill(local_time)
    if kind == "interval":
        form.locator('[name="interval_seconds"]').fill("1800")
    inputs = form.locator("#schedule-inputs")
    expect(inputs.locator('[name="input-key"]')).to_have_value("girdi")
    inputs.locator('[name="input-value"]').fill("evet")
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/schedules") and response.request.method == "POST"
        )
    ) as response:
        form.get_by_role("button", name="Zamanlamayı kaydet", exact=True).click()
    assert response.value.status == 201
    schedule = response.value.json()["schedule"]
    expect(form).not_to_be_visible()
    page.locator('[data-nav="schedules"]').click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Zamanlamalar")
    card = page.locator(f'[data-schedule-id="{schedule["id"]}"]')
    expect(card).to_contain_text(name)
    return schedule, card


def test_create_pause_resume_edit_and_pin_a_schedule_revision(page, console_url):
    from playwright.sync_api import expect

    flow_id = _flow(page)
    schedule, card = _schedule(page, kind="interval")
    assert schedule["flow_id"] == flow_id
    assert schedule["version"] == 1
    assert schedule["interval_seconds"] == 1800
    assert schedule["inputs"] == {"girdi": "evet"}

    card.locator('[data-schedule-action="pause"]').click()
    expect(card.locator('[data-schedule-action="resume"]')).to_be_visible()
    paused = page.request.get(f"{console_url}/api/schedules/{schedule['id']}").json()["schedule"]
    assert paused["enabled"] is False
    card.locator('[data-schedule-action="resume"]').click()
    expect(card.locator('[data-schedule-action="pause"]')).to_be_visible()

    page.goto(f"{console_url}/#flows/{flow_id}")
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 1 · Kaydedildi")
    page.locator("#flow-name").fill("İkinci sürümün farklı adı")
    _select(page, "yes")
    page.locator("#flow-value").fill("İkinci sürümün farklı sonucu")
    _save(page, 2)
    page.locator('[data-nav="schedules"]').click()
    expect(card).to_be_visible()
    card.locator('[data-schedule-action="edit"]').click()
    form = page.locator("#schedule-form")
    expect(form).to_be_visible()
    expect(form.locator('[name="interval_seconds"]')).to_have_value("1800")
    expect(form.locator('#schedule-inputs [name="input-value"]')).to_have_value("evet")
    form.locator('[name="name"]').fill("Güncellenmiş zamanlama")
    form.locator('[name="interval_seconds"]').fill("3600")
    form.get_by_role("button", name="Zamanlamayı kaydet", exact=True).click()
    expect(form).not_to_be_visible()
    expect(card).to_contain_text("Güncellenmiş zamanlama")
    updated = page.request.get(f"{console_url}/api/schedules/{schedule['id']}").json()["schedule"]
    assert updated["version"] == 1
    assert updated["interval_seconds"] == 3600
    assert updated["inputs"] == {"girdi": "evet"}
    assert updated["enabled"] is True


def test_once_schedule_converts_local_time_and_executes_the_saved_flow(
    istanbul_page, console_url, session_factory
):
    from playwright.sync_api import expect

    # datetime-local means the user's wall clock, not UTC. Exercise a browser
    # timezone different from both the test server and default browser fixture.
    page = istanbul_page
    flow_id = _flow(page)
    local_time = "2040-02-03T11:45"
    schedule, card = _schedule(page, local_time=local_time)
    expected_utc = page.evaluate("value => new Date(value).toISOString()", local_time)
    assert datetime.fromisoformat(schedule["next_run_at"]) == datetime.fromisoformat(
        expected_utc.replace("Z", "+00:00")
    )
    assert schedule["last_job_id"] is None

    # The only clock manipulation is in the harness's confirmed rpa_test_*
    # database. This avoids sleeping until a production-style timer expires.
    from app.database.models import FlowSchedule
    from app.database.repositories.job_repository import JobRepository

    with session_factory.begin() as session:
        session.execute(
            update(FlowSchedule)
            .where(FlowSchedule.id == UUID(schedule["id"]))
            .values(next_run_at=JobRepository(session).now())
        )

    expect(card.locator("[data-last-job]")).to_be_visible(timeout=20_000)
    dispatched = page.request.get(f"{console_url}/api/schedules/{schedule['id']}").json()[
        "schedule"
    ]
    assert dispatched["enabled"] is False
    assert dispatched["last_job_id"] is not None
    job_id = dispatched["last_job_id"]
    card.locator("[data-last-job]").click()
    expect(page).to_have_url(f"{console_url}/#jobs/{job_id}")
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Tamamlandı", timeout=20_000)
    records = page.request.get(f"{console_url}/api/jobs/{job_id}/transactions").json()
    assert records["total"] == 1
    assert records["items"][0]["status"] == "SUCCESS"
    assert records["items"][0]["attempt_count"] == 1
    assert records["items"][0]["result_data"] == {"sonuc": "Zamanlanan ilk sürüm"}
    run = page.request.get(f"{console_url}/api/flows/runs/by-job/{job_id}").json()
    assert run["flow_id"] == flow_id
    assert run["version"] == 1

    page.locator('[data-nav="schedules"]').click()
    expect(card).to_be_visible()
    _assert_no_horizontal_overflow(page)
    expect(page.locator("#toasts > *")).to_have_count(0, timeout=10_000)
    artifacts = PROJECT_ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    page.screenshot(path=str(artifacts / "schedules-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    expect(card.locator("[data-last-job]")).to_be_visible()
    page.screenshot(path=str(artifacts / "schedules-mobile.png"), full_page=True)


def test_failed_flow_selection_cannot_schedule_the_previous_flow(page, console_url):
    from playwright.sync_api import expect

    unavailable_flow_id = _flow(page)
    _flow(page)
    page.locator('[data-nav="schedules"]').click()
    page.locator('[data-schedule-action="new"]').first.click()
    form = page.locator("#schedule-form")
    expect(form).to_be_visible()
    selector = form.locator('select[name="flow_id"]')
    assert selector.input_value() != unavailable_flow_id
    page.route(
        f"{console_url}/api/flows/{unavailable_flow_id}",
        lambda route: route.abort("failed"),
    )
    selector.select_option(unavailable_flow_id)
    expect(form.locator("#schedule-error")).not_to_be_empty()
    submitted = []
    page.on(
        "request",
        lambda request: (
            submitted.append(request)
            if request.method == "POST" and request.url.endswith("/api/schedules")
            else None
        ),
    )
    submit = form.get_by_role("button", name="Zamanlamayı kaydet", exact=True)
    if submit.is_enabled():
        form.locator('[name="next_run_at"]').fill("2040-02-03T11:45")
        submit.click()
    expect(form).to_be_visible()
    assert not submitted, "A failed flow selection must not schedule the previous flow."
