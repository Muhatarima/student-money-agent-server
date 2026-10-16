import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import passkeys
from app.crypto import decrypt_str, hash_token
from app.models import AuditEvent, LinkToken, Passkey, User
from tests.conftest import KEY, ORIGIN
from tests.factories import WA_ID, make_user, phone_hash
from tests.fake_webauthn import challenge_of, make_credential


async def new_link(client, ph=None) -> str:
    body = {"phone_hash": ph or phone_hash(), "wa_id": WA_ID}
    response = await client.post("/internal/link-tokens", json=body, headers=KEY)
    return response.json()["url"].split("t=")[1]


async def post(client, path, body, headers=ORIGIN):
    return await client.post(path, json=body, headers=headers)


async def register(client, token, credential_id=b"cred-1"):
    options = (await post(client, "/passkeys/register/options", {"t": token})).json()
    credential = make_credential(challenge_of(options), credential_id)
    return await post(client, "/passkeys/register/verify", {"t": token, "credential": credential})


async def expire_all_link_tokens(session):
    past = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(update(LinkToken).values(expires_at=past))


# --- Link page ---


async def test_link_page_shows_the_button(client):
    token = await new_link(client)
    response = await client.get(f"/link?t={token}")
    assert response.status_code == 200
    assert "Use fingerprint / Face ID" in response.text
    assert 'integrity="sha384-' in response.text  # pinned script with SRI


async def test_link_page_unknown_token(client):
    assert (await client.get("/link?t=nope")).status_code == 410


async def test_link_page_expired_token(client, session):
    token = await new_link(client)
    await expire_all_link_tokens(session)
    assert (await client.get(f"/link?t={token}")).status_code == 410


# --- Registration ---


async def test_register_links_the_account(client, session, redis):
    ph = phone_hash()
    token = await new_link(client, ph)
    response = await register(client, token)
    assert response.json() == {"ok": True, "redirect": "/done"}

    user = await session.scalar(select(User).where(User.phone_hash == ph))
    assert user.linked is True and decrypt_str(user.wa_id_encrypted) == WA_ID
    passkey = await session.scalar(select(Passkey).where(Passkey.user_id == user.id))
    assert passkey.credential_id == b"cred-1" and json.loads(passkey.transports) == ["internal"]
    link = await session.scalar(select(LinkToken).where(LinkToken.token_hash == hash_token(token)))
    assert link.used_at is not None
    last = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    assert last.action == "passkey.registered"
    [(_, fields)] = await redis.xrange("events:notify")
    assert json.loads(fields["data"])["type"] == "account_linked"


async def test_done_page(client):
    assert "All set" in (await client.get("/done")).text


async def test_token_reuse_fails(client):
    token = await new_link(client)
    options = (await post(client, "/passkeys/register/options", {"t": token})).json()
    credential = make_credential(challenge_of(options))
    body = {"t": token, "credential": credential}
    assert (await post(client, "/passkeys/register/verify", body)).status_code == 200
    # Used: neither a new start nor a replay of the same answer works
    assert (await post(client, "/passkeys/register/options", {"t": token})).status_code == 410
    assert (await post(client, "/passkeys/register/verify", body)).status_code == 410


async def test_expired_token_fails(client, session):
    token = await new_link(client)
    await expire_all_link_tokens(session)
    assert (await post(client, "/passkeys/register/options", {"t": token})).status_code == 410


async def test_challenge_works_once_only(client):
    token = await new_link(client)
    options = (await post(client, "/passkeys/register/options", {"t": token})).json()
    wrong = make_credential(b"some other challenge")
    response = await post(client, "/passkeys/register/verify", {"t": token, "credential": wrong})
    assert response.status_code == 400
    # The right answer now is too late: that challenge is gone
    right = make_credential(challenge_of(options))
    response = await post(client, "/passkeys/register/verify", {"t": token, "credential": right})
    assert response.status_code == 400


async def test_wrong_origin_in_signed_data_fails(client):
    token = await new_link(client)
    options = (await post(client, "/passkeys/register/options", {"t": token})).json()
    phishing = make_credential(challenge_of(options), origin="https://evil.example")
    response = await post(client, "/passkeys/register/verify", {"t": token, "credential": phishing})
    assert response.status_code == 400


async def test_second_device_adds_a_passkey_to_the_same_user(client, session):
    ph = phone_hash()
    await register(client, await new_link(client, ph), b"phone")
    await register(client, await new_link(client, ph), b"laptop")
    users = (await session.scalars(select(User).where(User.phone_hash == ph))).all()
    assert len(users) == 1
    keys = await session.scalars(
        select(Passkey.credential_id).where(Passkey.user_id == users[0].id)
    )
    assert sorted(keys) == [b"laptop", b"phone"]


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}])
async def test_post_from_another_site_is_403(client, headers):
    token = await new_link(client)
    response = await post(client, "/passkeys/register/options", {"t": token}, headers=headers)
    assert response.status_code == 403


async def test_post_must_be_json(client):
    response = await client.post("/passkeys/register/options", data={"t": "x"}, headers=ORIGIN)
    assert response.status_code == 415


# --- Login helpers ---


async def user_with_passkey(session):
    user = await make_user(session)
    credential_id = uuid.uuid4().bytes
    session.add(
        Passkey(user_id=user.id, credential_id=credential_id, public_key=b"pk-" + credential_id)
    )
    await session.flush()
    return user, credential_id


async def test_start_auth_lists_only_this_users_passkeys(session):
    user, cid = await user_with_passkey(session)
    await user_with_passkey(session)  # someone else
    options = await passkeys.start_auth(session, user.id, b"c" * 32)
    assert [c["id"] for c in options["allowCredentials"]] == [make_credential(b"", cid)["id"]]
    assert options["userVerification"] == "required"
    assert challenge_of(options) == b"c" * 32


async def test_finish_auth_ok_updates_sign_count(session):
    user, cid = await user_with_passkey(session)
    passkey = await passkeys.finish_auth(session, user.id, make_credential(b"chal", cid), b"chal")
    assert passkey.sign_count == 1 and passkey.last_used_at is not None


async def test_finish_auth_wrong_challenge_fails(session):
    user, cid = await user_with_passkey(session)
    with pytest.raises(passkeys.AuthFailed):
        await passkeys.finish_auth(session, user.id, make_credential(b"other", cid), b"chal")


async def test_finish_auth_someone_elses_passkey_fails(session):
    user, _ = await user_with_passkey(session)
    _, other_cid = await user_with_passkey(session)
    with pytest.raises(passkeys.AuthFailed):
        await passkeys.finish_auth(session, user.id, make_credential(b"chal", other_cid), b"chal")


@pytest.mark.parametrize("credential", [{}, {"rawId": 123}, {"rawId": "!!not base64!!"}])
async def test_finish_auth_garbage_fails(session, credential):
    user, _ = await user_with_passkey(session)
    with pytest.raises(passkeys.AuthFailed):
        await passkeys.finish_auth(session, user.id, credential, b"chal")
