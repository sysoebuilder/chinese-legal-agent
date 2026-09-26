import asyncio
import smtplib
from unittest.mock import Mock

import pytest

from ai_agent_learning.langchain_agent.tools import send_email


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("QQ_SMTP_USERNAME", "sender@qq.com")
    monkeypatch.setenv("QQ_SMTP_AUTH_CODE", "test-secret")
    client = Mock()
    client.send_message.return_value = {}
    factory = Mock(return_value=client)
    monkeypatch.setattr(send_email.smtplib, "SMTP_SSL", factory)
    return client, factory


def invoke(attachment_path=None):
    return asyncio.run(send_email.handler(
        "receiver@example.com", "学习笔记", "今天学习工具调用。", attachment_path,
    ))


def test_sends_unicode_body_and_attachment(smtp, tmp_path):
    client, factory = smtp
    attachment = tmp_path / "笔记.txt"
    attachment.write_bytes(b"notes")
    result = invoke(str(attachment))
    message = client.send_message.call_args.args[0]
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == (
        "今天学习工具调用。"
    )
    part = next(message.iter_attachments())
    assert part.get_filename() == "笔记.txt"
    assert part.get_payload(decode=True) == b"notes"
    assert result["status"] == "accepted"
    assert result["message_id"] == message["Message-ID"]
    assert factory.call_args.args == ("smtp.qq.com", 465)
    assert factory.call_args.kwargs["context"].check_hostname
    client.close.assert_called_once()


def test_sends_without_attachment(smtp):
    client, _ = smtp
    invoke()
    assert not list(client.send_message.call_args.args[0].iter_attachments())


def test_missing_config_never_connects(smtp, monkeypatch):
    _, factory = smtp
    monkeypatch.delenv("QQ_SMTP_AUTH_CODE")
    with pytest.raises(ValueError, match="QQ_SMTP_USERNAME"):
        invoke()
    factory.assert_not_called()


@pytest.mark.parametrize("oversized", [False, True])
def test_invalid_attachment_never_connects(smtp, tmp_path, monkeypatch, oversized):
    _, factory = smtp
    path = tmp_path / "attachment.bin"
    if oversized:
        monkeypatch.setattr(send_email, "MAX_ATTACHMENT_BYTES", 2)
        path.write_bytes(b"123")
    with pytest.raises(ValueError):
        invoke(str(path))
    factory.assert_not_called()


@pytest.mark.parametrize("stage, error, error_type, message", [
    ("login", smtplib.SMTPAuthenticationError(535, b"test-secret"),
     ValueError, "认证失败"),
    ("send_message", smtplib.SMTPServerDisconnected("test-secret"),
     RuntimeError, "状态未知"),
    ("send_message", smtplib.SMTPRecipientsRefused({"receiver@example.com": (550, b"no")}),
     ValueError, "拒绝"),
])
def test_send_failures_are_classified_and_connection_closed(
    smtp, stage, error, error_type, message
):
    client, _ = smtp
    getattr(client, stage).side_effect = error
    with pytest.raises(error_type, match=message) as caught:
        invoke()
    assert "test-secret" not in str(caught.value)
    client.close.assert_called_once()


def test_close_failure_does_not_invalidate_accepted_mail(smtp):
    client, _ = smtp
    client.close.side_effect = OSError("close failed")
    assert invoke()["status"] == "accepted"


def test_header_injection_never_connects(smtp):
    _, factory = smtp
    with pytest.raises(ValueError):
        asyncio.run(send_email.handler(
            "receiver@example.com", "Subject\r\nBcc: hidden@example.com", "正文",
        ))
    factory.assert_not_called()
