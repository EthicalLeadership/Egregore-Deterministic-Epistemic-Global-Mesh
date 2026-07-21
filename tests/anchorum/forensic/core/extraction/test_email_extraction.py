"""Tests for ANCHORUM email metadata extractor."""

from __future__ import annotations

from datetime import UTC, datetime

from anchorum.forensic.core.extraction.email import extract_email_metadata

SAMPLE_EMAIL = b"""From: alice@example.com
To: bob@example.com, carol@example.com
Cc: dave@example.com
Subject: Project Update
Date: Thu, 15 Jun 2023 10:30:00 +0000
Message-ID: <abc123@example.com>
X-Mailer: Microsoft Outlook 16.0
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"

Hi Bob,

Please review https://internal.example.com/claim and reply to eve@example.com.

Thanks,
Alice
"""


def test_extract_email_metadata_headers() -> None:
    extracted = extract_email_metadata(SAMPLE_EMAIL, "EMAIL-001")

    assert extracted.artifact_id == "EMAIL-001"
    assert extracted.plane_container is not None
    container = extracted.plane_container
    assert container.from_addr == "alice@example.com"
    assert "bob@example.com" in container.to_addrs
    assert "carol@example.com" in container.to_addrs
    assert container.subject == "Project Update"
    assert container.message_id == "<abc123@example.com>"
    assert container.x_mailer == "Microsoft Outlook 16.0"


def test_extract_email_metadata_temporal() -> None:
    extracted = extract_email_metadata(SAMPLE_EMAIL, "EMAIL-001")

    assert extracted.plane_temporal is not None
    events = extracted.plane_temporal.events
    assert any(e.event_type == "email_sent" for e in events)
    sent_event = next(e for e in events if e.event_type == "email_sent")
    assert sent_event.timestamp == datetime(2023, 6, 15, 10, 30, 0, tzinfo=UTC)


def test_extract_email_metadata_content() -> None:
    extracted = extract_email_metadata(SAMPLE_EMAIL, "EMAIL-001")

    assert extracted.plane_content is not None
    content = extracted.plane_content
    assert "https://internal.example.com/claim" in content.embedded_urls
    assert "eve@example.com" in content.email_addresses
    assert content.word_count is not None
    assert content.word_count > 5


def test_extract_email_metadata_application() -> None:
    extracted = extract_email_metadata(SAMPLE_EMAIL, "EMAIL-001")

    assert extracted.plane_application is not None
    assert extracted.plane_application.application == "Microsoft Outlook 16.0"
    assert extracted.plane_application.author == "alice@example.com"


def test_extract_email_metadata_received_chain() -> None:
    email_with_received = b"""From: alice@example.com
To: bob@example.com
Subject: Re: Update
Date: Thu, 15 Jun 2023 10:30:00 +0000
Received: from mail1.example.com by mx.example.com; Thu, 15 Jun 2023 10:25:00 +0000
Received: from client.example.com by mail1.example.com; Thu, 15 Jun 2023 10:20:00 +0000

Body text.
"""
    extracted = extract_email_metadata(email_with_received, "EMAIL-002")
    container = extracted.plane_container
    assert container is not None
    assert len(container.received_chain) == 2
    assert container.received_chain[0].by_host == "mx.example.com"
    assert container.received_chain[-1].from_host == "client.example.com"

    temporal = extracted.plane_temporal
    assert temporal is not None
    assert any(e.event_type == "email_received" for e in temporal.events)


def test_extract_email_metadata_multipart_attachment() -> None:
    email_multipart = b"""From: sender@example.com
To: recipient@example.com
Subject: Attachment Test
Date: Thu, 15 Jun 2023 11:00:00 +0000
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset="utf-8"

See attached document.

--boundary123
Content-Type: application/pdf; name="report.pdf"
Content-Disposition: attachment; filename="report.pdf"
Content-ID: <report-001>

%PDF-1.4 fake pdf content
--boundary123--
"""
    extracted = extract_email_metadata(email_multipart, "EMAIL-003")
    container = extracted.plane_container
    assert container is not None
    assert len(container.attachments) == 1
    assert container.attachments[0].filename == "report.pdf"
    assert container.attachments[0].content_id == "report-001"
    assert container.attachments[0].size is not None


def test_extract_email_metadata_minimal_input() -> None:
    extracted = extract_email_metadata(b"Subject: Hello\n\nBody", "EMAIL-MIN")
    assert extracted.plane_container is not None
    assert extracted.plane_container.subject == "Hello"
    assert extracted.extraction_errors == ()
