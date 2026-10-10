"""Paid-provider regressions use synthetic SQLite stores and offline clients only."""

from unittest.mock import Mock

import pytest

from seohead.data_sources.arsenkin import ArsenkinError
from seohead.data_sources.yandex_cloud import NetworkAmbiguousError
from seohead.semantics.stages import collect, exact, synonyms

pytestmark = pytest.mark.usefixtures("semantics_offline")


def seed_exact(sem_store, phrase="seo аудит сайта", **fields):
    sem_store.upsert(phrase, base=100)
    sem_store.set_fields(phrase, status="kept", **fields)
    sem_store.commit()


def response(phrase="seo аудит сайта", **values):
    return {"code": "TASK_RESULT", "result": {"data": {"result": {phrase: {"225": values}}}}}


def client(monkeypatch, raw=None):
    fake = Mock()
    fake.set_task.return_value = {"task_id": 314, "cost": 3}
    fake.wait.return_value = raw if raw is not None else response(quoted=17, overal=0)
    monkeypatch.setattr(exact, "ArsenkinClient", lambda: fake)
    return fake


def test_timeout_resumes_paid_task_without_new_submission(sem_store, sem_cfg, monkeypatch):
    seed_exact(sem_store)
    fake = client(monkeypatch)
    fake.wait.side_effect = [
        ArsenkinError("TIMEOUT", "already paid"),
        response(quoted=17, overal=0),
    ]
    assert exact.run(sem_store, sem_cfg)["pending"] == 1
    assert sem_store.spend()["arsenkin"] == 3
    assert exact.run(sem_store, sem_cfg)["snapped"] == 1
    assert fake.set_task.call_count == 1
    assert [call.args[0] for call in fake.wait.call_args_list] == [314, 314]
    task = sem_store.db.execute("SELECT * FROM provider_tasks").fetchone()
    assert task["state"] == "complete" and task["result_json"]
    assert sem_store.spend()["arsenkin"] == 3


def test_unknown_submission_is_not_automatically_resubmitted(sem_store, sem_cfg, monkeypatch):
    seed_exact(sem_store)
    fake = client(monkeypatch)
    fake.set_task.side_effect = ArsenkinError("NETWORK_AMBIGUOUS", "response lost")
    assert exact.run(sem_store, sem_cfg)["blocked"] == 1
    assert exact.run(sem_store, sem_cfg)["blocked"] == 1
    assert fake.set_task.call_count == 1
    fake.wait.assert_not_called()


def test_interrupted_submission_reservation_prevents_another_charge(
    sem_store, sem_cfg, monkeypatch
):
    seed_exact(sem_store)
    fake = client(monkeypatch)
    fake.set_task.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        exact.run(sem_store, sem_cfg)
    fake.set_task.side_effect = None
    assert exact.run(sem_store, sem_cfg)["blocked"] == 1
    assert fake.set_task.call_count == 1


def test_paid_malformed_payload_is_retained_and_not_resubmitted(sem_store, sem_cfg, monkeypatch):
    seed_exact(sem_store)
    fake = client(monkeypatch, raw={"unexpected": "shape"})
    assert exact.run(sem_store, sem_cfg)["snapped"] == 0
    assert exact.run(sem_store, sem_cfg)["snapped"] == 0
    assert fake.set_task.call_count == fake.wait.call_count == 1
    assert sem_store.db.execute("SELECT exact FROM phrases").fetchone()[0] is None
    task = sem_store.db.execute("SELECT * FROM provider_tasks").fetchone()
    assert "unexpected" in task["result_json"]
    assert task["error"]
    assert sem_store.spend()["arsenkin"] == 3


def test_parser_failure_keeps_cost_and_raw_for_safe_recovery(sem_store, sem_cfg, monkeypatch):
    seed_exact(sem_store)
    fake = client(monkeypatch)
    parser = exact.parse_wordstat
    monkeypatch.setattr(exact, "parse_wordstat", Mock(side_effect=ValueError("malformed")))
    with pytest.raises(ValueError, match="malformed"):
        exact.run(sem_store, sem_cfg)
    assert sem_store.spend()["arsenkin"] == 3
    task = sem_store.db.execute("SELECT * FROM provider_tasks").fetchone()
    assert task["task_id"] == 314 and task["result_json"] and task["state"] == "received"
    monkeypatch.setattr(exact, "parse_wordstat", parser)
    assert exact.run(sem_store, sem_cfg)["snapped"] == 1
    assert fake.set_task.call_count == fake.wait.call_count == 1


def test_zero_null_and_existing_measurements_are_distinct(sem_store, sem_cfg, monkeypatch):
    seed_exact(sem_store, lemma_group=1, exact=40, quoted=90)
    seed_exact(sem_store, "seo аудит сайта новый", lemma_group=1)
    seed_exact(sem_store, "seo аудит сайта маленький", lemma_group=2)
    raw = {
        "result": {
            "data": {
                "result": {
                    "seo аудит сайта новый": {"225": {"quoted": 17, "overal": 0}},
                    "seo аудит сайта маленький": {"225": {"quoted": 5}},
                }
            }
        }
    }
    client(monkeypatch, raw)
    assert exact.run(sem_store, sem_cfg)["snapped"] == 1
    rows = {r["norm"]: (r["exact"], r["quoted"]) for r in sem_store.phrases()}
    assert rows == {
        "seo аудит сайта": (40, 90),
        "seo аудит сайта новый": (0, 17),
        "seo аудит сайта маленький": (None, 5),
    }


def test_real_demand_prevents_exact_dead_cutoff(sem_store, sem_cfg, monkeypatch):
    seed_exact(sem_store, base=5, pos_g=8)
    client(monkeypatch)
    assert exact.run(sem_store, sem_cfg)["dead"] == 0
    assert sem_store.db.execute("SELECT status FROM phrases").fetchone()[0] == "kept"


def test_collect_normalizes_queue_and_keeps_stopped_results(sem_store, sem_cfg, monkeypatch):
    sem_cfg.update(preset="generic", stop="мусор", max_depth=1)
    fake = Mock()
    fake.expand.return_value = (
        {"SEO-аудит-сайта": 100, "seo аудит сайта": 90, "мусор аудит": 80},
        {"results": 3, "associations": 0},
    )
    monkeypatch.setattr(collect, "Wordstat", lambda **kwargs: fake)
    collect.run(sem_store, sem_cfg, seeds=["SEO-аудит-сайта", "seo аудит сайта"])
    assert fake.expand.call_count == 1
    row = sem_store.db.execute("SELECT * FROM phrases WHERE norm='мусор аудит'").fetchone()
    assert row["status"] == "dropped" and row["reason"] and row["reason_stage"] == "collect"
    assert sem_store.spend()["yandex_requests"] == 1


def test_collect_ambiguous_response_blocks_automatic_retry(sem_store, sem_cfg, monkeypatch):
    fake = Mock()
    fake.expand.side_effect = NetworkAmbiguousError("response lost")
    monkeypatch.setattr(collect, "Wordstat", lambda **kwargs: fake)
    assert collect.run(sem_store, sem_cfg, seeds=["seo аудит сайта"])["blocked"] == 1
    assert collect.run(sem_store, sem_cfg, seeds=["seo аудит сайта"], resume=True)["blocked"] == 1
    assert fake.expand.call_count == 1
    assert not sem_store.is_expanded("seo аудит сайта")


def test_successful_collect_cost_survives_later_processing_error(sem_store, sem_cfg, monkeypatch):
    fake = Mock()
    fake.expand.return_value = ({"аудит сайта другой": 80}, {"results": 1, "associations": 0})
    monkeypatch.setattr(collect, "Wordstat", lambda **kwargs: fake)
    monkeypatch.setattr(sem_store, "add_edge", Mock(side_effect=ValueError("write failed")))
    with pytest.raises(ValueError, match="write failed"):
        collect.run(sem_store, sem_cfg, seeds=["seo аудит сайта"])
    sem_store.db.rollback()
    assert sem_store.spend()["yandex_requests"] == 1
    assert sem_store.db.execute("SELECT state FROM collect_attempts").fetchone()[0] == "submitting"


def test_synonym_progress_survives_interrupt_and_missing_is_not_zero(
    sem_store, sem_cfg, tmp_path, monkeypatch
):
    (tmp_path / "synonyms.txt").write_text(
        "аудит сайта большой\nаудит сайта маленький\n", encoding="utf-8"
    )
    fake = Mock()
    fake.top.side_effect = [{"totalCount": None}, KeyboardInterrupt]
    monkeypatch.setattr(synonyms, "Wordstat", lambda **kwargs: fake)
    with pytest.raises(KeyboardInterrupt):
        synonyms.run(sem_store, sem_cfg)
    row = sem_store.db.execute("SELECT * FROM phrases WHERE norm='аудит сайта большой'").fetchone()
    assert row["base"] is None and row["status"] == "review"
    assert sem_store.spend()["yandex_requests"] == 1
    assert synonyms.run(sem_store, sem_cfg)["checked"] == 0
    assert fake.top.call_count == 2


def test_billed_invalid_task_id_retains_charge_without_resubmission(
    sem_store, sem_cfg, monkeypatch
):
    seed_exact(sem_store)
    fake = client(monkeypatch)
    fake.set_task.side_effect = ArsenkinError(
        "INVALID_TASK_ID", "billed without an ID", {"task_id": None, "cost": 7}
    )
    assert exact.run(sem_store, sem_cfg)["blocked"] == 1
    assert exact.run(sem_store, sem_cfg)["blocked"] == 1
    assert fake.set_task.call_count == 1
    assert sem_store.spend()["arsenkin"] == 7
    assert sem_store.db.execute("SELECT cost FROM provider_tasks").fetchone()[0] == 7
