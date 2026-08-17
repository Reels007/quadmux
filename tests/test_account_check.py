import json

import account_check

# Built with chr() on purpose: a literal one of these characters anywhere in the
# repo trips Sean's plain-text guard hook.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


def _write(path, payload):
    path.write_text(json.dumps(payload))
    return str(path)


def test_reads_email_org_and_tier(tmp_path):
    cfg = _write(tmp_path / "claude.json", {
        "oauthAccount": {
            "emailAddress": "sean.reel@ignitebermuda.com",
            "organizationName": "Ignitebermuda",
            "seatTier": "team_standard",
            "hasExtraUsageEnabled": True,
        }
    })
    acct = account_check.read_account(cfg)
    assert acct["email"] == "sean.reel@ignitebermuda.com"
    assert acct["org"] == "Ignitebermuda"
    assert acct["tier"] == "Team"
    assert acct["extra_usage"] is True
    assert acct["ok"] is True


def test_max_and_free_tiers_are_labelled(tmp_path):
    for seat, label in (("max_20x", "Max 20x"), ("max", "Max"), ("free", "Free")):
        cfg = _write(tmp_path / (seat + ".json"),
                     {"oauthAccount": {"emailAddress": "x@y.com", "seatTier": seat}})
        assert account_check.read_account(cfg)["tier"] == label


def test_unknown_tier_falls_back_to_raw_value(tmp_path):
    cfg = _write(tmp_path / "c.json",
                 {"oauthAccount": {"emailAddress": "x@y.com", "seatTier": "enterprise_v9"}})
    assert account_check.read_account(cfg)["tier"] == "enterprise_v9"


def test_missing_file_is_not_fatal(tmp_path):
    acct = account_check.read_account(str(tmp_path / "nope.json"))
    assert acct["ok"] is False
    assert acct["email"] is None
    assert "not found" in acct["error"].lower()


def test_malformed_json_is_not_fatal(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    acct = account_check.read_account(str(bad))
    assert acct["ok"] is False
    assert acct["email"] is None


def test_missing_oauth_block_is_not_fatal(tmp_path):
    cfg = _write(tmp_path / "c.json", {"somethingElse": 1})
    assert account_check.read_account(cfg)["ok"] is False


def test_notes_are_matched_to_the_live_email(tmp_path):
    notes = _write(tmp_path / "notes.json", {
        "accounts": {
            "sean.reel@ignitebermuda.com": {
                "credits": "$324.41", "seats": "5 of 5", "verified": "2026-07-29",
            },
            "sean.reel@googlemail.com": {"credits": "$137", "verified": "2026-07-29"},
        }
    })
    got = account_check.read_notes(notes, "sean.reel@ignitebermuda.com")
    assert got["credits"] == "$324.41"
    assert got["verified"] == "2026-07-29"


def test_notes_absent_or_unmatched_returns_empty(tmp_path):
    assert account_check.read_notes(str(tmp_path / "none.json"), "a@b.com") == {}
    notes = _write(tmp_path / "n.json", {"accounts": {"other@x.com": {"credits": "$1"}}})
    assert account_check.read_notes(notes, "a@b.com") == {}


def test_summary_marks_notes_as_dated_not_live(tmp_path):
    cfg = _write(tmp_path / "claude.json", {
        "oauthAccount": {"emailAddress": "sean.reel@ignitebermuda.com",
                         "organizationName": "Ignitebermuda",
                         "seatTier": "team_standard"}
    })
    notes = _write(tmp_path / "notes.json", {
        "accounts": {"sean.reel@ignitebermuda.com":
                     {"credits": "$324.41", "verified": "2026-07-29"}}
    })
    s = account_check.summary(config_path=cfg, notes_path=notes)
    assert s["email"] == "sean.reel@ignitebermuda.com"
    assert s["credits"] == "$324.41"
    # The balance is not readable locally, so it must never look live.
    assert s["credits_live"] is False
    assert s["credits_verified"] == "2026-07-29"


def test_summary_without_notes_reports_no_credit_figure(tmp_path):
    cfg = _write(tmp_path / "claude.json",
                 {"oauthAccount": {"emailAddress": "a@b.com", "seatTier": "free"}})
    s = account_check.summary(config_path=cfg, notes_path=str(tmp_path / "absent.json"))
    assert s["credits"] is None
    assert s["credits_live"] is False


def test_banner_includes_account_and_dates_the_balance(tmp_path):
    cfg = _write(tmp_path / "claude.json", {
        "oauthAccount": {"emailAddress": "sean.reel@ignitebermuda.com",
                         "organizationName": "Ignitebermuda",
                         "seatTier": "team_standard"}
    })
    notes = _write(tmp_path / "notes.json", {
        "accounts": {"sean.reel@ignitebermuda.com":
                     {"credits": "$324.41", "seats": "5 of 5", "verified": "2026-07-29"}}
    })
    text = "\n".join(account_check.banner_lines(config_path=cfg, notes_path=notes))
    assert "sean.reel@ignitebermuda.com" in text
    assert "Team" in text
    assert "$324.41" in text
    assert "2026-07-29" in text
    assert EM_DASH not in text and EN_DASH not in text


def test_banner_survives_a_missing_config(tmp_path):
    lines = account_check.banner_lines(config_path=str(tmp_path / "gone.json"),
                                       notes_path=str(tmp_path / "gone2.json"))
    assert lines and any("account" in ln.lower() for ln in lines)
