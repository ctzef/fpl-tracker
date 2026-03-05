"""
FPL Email Report Generator
Creates HTML email reports for daily FPL summaries.
Supports sending via SMTP (Gmail, SendGrid, etc.) or Resend API.
"""
from __future__ import annotations

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def generate_email_html(data: dict) -> str:
    """
    Generate a polished HTML email from the processed FPL data.
    
    Args:
        data: Output from FPLProcessor.process_all() with predictions applied.
    
    Returns:
        HTML string for the email body.
    """
    meta = data.get("meta", {})
    next_gw = meta.get("nextGW", "?")
    updated = meta.get("updatedAt", "")
    captain_picks = data.get("captainPicks", [])[:3]
    injuries = data.get("injuries", [])[:5]
    differentials = data.get("differentials", [])[:5]
    players = data.get("players", [])[:10]
    fixtures = data.get("fixtures", [])

    # Format date
    try:
        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        date_str = dt.strftime("%A %d %B %Y, %H:%M UTC")
    except (ValueError, TypeError):
        date_str = updated

    # FDR color mapping
    def fdr_color(fdr):
        return {1: "#257d5a", 2: "#00b87a", 3: "#B0A050", 4: "#e8553d", 5: "#8b1538"}.get(fdr, "#555")

    def momentum_emoji(m):
        return {"hot": "🔥", "rising": "📈", "stable": "➡️", "cooling": "📉", "declining": "⬇️", "volatile": "🎲"}.get(m, "—")

    # ── Build HTML ──────────────────────────────────────────────
    captain_rows = ""
    for i, p in enumerate(captain_picks):
        bg = "#1a2332" if i % 2 == 0 else "#151d2a"
        rank_bg = "#00b87a" if i == 0 else "#2a3545"
        rank_color = "#080c14" if i == 0 else "#8B9DAF"
        captain_rows += f"""
        <tr style="background:{bg};">
            <td style="padding:10px 12px;border-bottom:1px solid #1e2d3d;">
                <span style="display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;border-radius:4px;background:{rank_bg};color:{rank_color};font-size:12px;font-weight:700;">{i+1}</span>
            </td>
            <td style="padding:10px 8px;border-bottom:1px solid #1e2d3d;color:#fff;font-weight:600;">{p['webName'] or p['name']}</td>
            <td style="padding:10px 8px;border-bottom:1px solid #1e2d3d;color:#8B9DAF;">{p['team']}</td>
            <td style="padding:10px 8px;border-bottom:1px solid #1e2d3d;color:#8B9DAF;">{p['nextFixture']}</td>
            <td style="padding:10px 8px;border-bottom:1px solid #1e2d3d;">
                <span style="background:{fdr_color(p['fdr'])};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;">{p['fdr']}</span>
            </td>
            <td style="padding:10px 8px;border-bottom:1px solid #1e2d3d;color:#00b87a;font-weight:800;font-size:16px;">{p['predictedPts']}</td>
        </tr>"""

    injury_rows = ""
    if injuries:
        for p in injuries:
            injury_rows += f"""
            <tr style="background:#1a1520;">
                <td style="padding:8px 12px;border-bottom:1px solid #2d1e2e;color:#fff;font-weight:600;">{p['webName'] or p['name']}</td>
                <td style="padding:8px 8px;border-bottom:1px solid #2d1e2e;color:#8B9DAF;">{p['team']} · {p['position']}</td>
                <td style="padding:8px 8px;border-bottom:1px solid #2d1e2e;color:#EF5350;font-size:12px;">{p['injuryNote'] or 'No details'}</td>
            </tr>"""
    else:
        injury_rows = '<tr><td colspan="3" style="padding:16px;text-align:center;color:#5a6b7f;">No injuries to report ✅</td></tr>'

    diff_rows = ""
    for p in differentials:
        diff_rows += f"""
        <tr style="background:#151d2a;">
            <td style="padding:8px 12px;border-bottom:1px solid #1e2d3d;color:#fff;font-weight:600;">{p['webName'] or p['name']}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#8B9DAF;">{p['team']} · {p['position']}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#8B9DAF;">{p['ownership']}%</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#00b87a;font-weight:700;">{p['predictedPts']}</td>
        </tr>"""

    top_predicted_rows = ""
    for i, p in enumerate(players[:10]):
        bg = "#1a2332" if i % 2 == 0 else "#151d2a"
        top_predicted_rows += f"""
        <tr style="background:{bg};">
            <td style="padding:8px 12px;border-bottom:1px solid #1e2d3d;color:#5a6b7f;font-weight:700;">{i+1}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#fff;font-weight:600;">{p['webName'] or p['name']}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#8B9DAF;">{p['team']}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#8B9DAF;">{p['position']}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#8B9DAF;">{p['nextFixture']}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;">{momentum_emoji(p['momentum'])}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#00b87a;font-weight:700;">{p['predictedPts']}</td>
        </tr>"""

    fixture_rows = ""
    for f in fixtures:
        home_euro = f'<span style="color:#64B5F6;font-size:9px;margin-left:4px;">UCL</span>' if f.get("homeEuro") else ""
        away_euro = f'<span style="color:#64B5F6;font-size:9px;margin-left:4px;">UCL</span>' if f.get("awayEuro") else ""
        fixture_rows += f"""
        <tr style="background:#151d2a;">
            <td style="padding:8px 12px;border-bottom:1px solid #1e2d3d;text-align:right;color:#fff;font-weight:600;">{f['home']}{home_euro}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;text-align:center;color:#5a6b7f;">{f['time']}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;color:#fff;font-weight:600;">{f['away']}{away_euro}</td>
            <td style="padding:8px 8px;border-bottom:1px solid #1e2d3d;text-align:center;">
                <span style="background:{fdr_color(f['awayFdr'])};color:#fff;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;">{f['awayFdr']}</span>
                /
                <span style="background:{fdr_color(f['homeFdr'])};color:#fff;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;">{f['homeFdr']}</span>
            </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#080c14;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:640px;margin:0 auto;background:#0d1421;border:1px solid #1e2d3d;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#0d2818,#0d1421);padding:28px 24px;border-bottom:1px solid #1e2d3d;">
        <div style="display:flex;align-items:center;">
            <span style="font-size:28px;margin-right:12px;">⚽</span>
            <div>
                <div style="font-size:24px;font-weight:900;color:#fff;letter-spacing:-0.5px;">FPL <span style="color:#00b87a;">Tracker</span></div>
                <div style="font-size:12px;color:#5a6b7f;margin-top:2px;">Gameweek {next_gw} Daily Briefing</div>
            </div>
        </div>
        <div style="margin-top:12px;font-size:11px;color:#5a6b7f;">{date_str}</div>
    </div>

    <!-- Captain Picks -->
    <div style="padding:24px;">
        <div style="font-size:12px;font-weight:700;color:#5a6b7f;letter-spacing:1.5px;margin-bottom:12px;">👑 CAPTAIN PICKS — GW{next_gw}</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
                <tr style="background:#111827;">
                    <th style="padding:8px 12px;text-align:left;color:#5a6b7f;font-size:10px;font-weight:600;border-bottom:1px solid #1e2d3d;">#</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;font-weight:600;border-bottom:1px solid #1e2d3d;">PLAYER</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;font-weight:600;border-bottom:1px solid #1e2d3d;">TEAM</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;font-weight:600;border-bottom:1px solid #1e2d3d;">FIXTURE</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;font-weight:600;border-bottom:1px solid #1e2d3d;">FDR</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;font-weight:600;border-bottom:1px solid #1e2d3d;">PRED</th>
                </tr>
            </thead>
            <tbody>{captain_rows}</tbody>
        </table>
    </div>

    <!-- Injury Alerts -->
    <div style="padding:0 24px 24px;">
        <div style="font-size:12px;font-weight:700;color:#5a6b7f;letter-spacing:1.5px;margin-bottom:12px;">🚑 INJURY ALERTS</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <tbody>{injury_rows}</tbody>
        </table>
    </div>

    <!-- Differentials -->
    <div style="padding:0 24px 24px;">
        <div style="font-size:12px;font-weight:700;color:#5a6b7f;letter-spacing:1.5px;margin-bottom:12px;">💎 DIFFERENTIALS</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
                <tr style="background:#111827;">
                    <th style="padding:8px 12px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">PLAYER</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">INFO</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">OWNED</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">PRED</th>
                </tr>
            </thead>
            <tbody>{diff_rows}</tbody>
        </table>
    </div>

    <!-- Top 10 Predicted -->
    <div style="padding:0 24px 24px;">
        <div style="font-size:12px;font-weight:700;color:#5a6b7f;letter-spacing:1.5px;margin-bottom:12px;">📊 TOP 10 PREDICTED POINTS</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
                <tr style="background:#111827;">
                    <th style="padding:8px 12px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">#</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">PLAYER</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">TEAM</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">POS</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">FIX</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">FORM</th>
                    <th style="padding:8px 8px;text-align:left;color:#5a6b7f;font-size:10px;border-bottom:1px solid #1e2d3d;">PRED</th>
                </tr>
            </thead>
            <tbody>{top_predicted_rows}</tbody>
        </table>
    </div>

    <!-- Fixtures -->
    <div style="padding:0 24px 24px;">
        <div style="font-size:12px;font-weight:700;color:#5a6b7f;letter-spacing:1.5px;margin-bottom:12px;">📅 GW{next_gw} FIXTURES</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <tbody>{fixture_rows}</tbody>
        </table>
    </div>

    <!-- Footer -->
    <div style="padding:20px 24px;border-top:1px solid #1e2d3d;text-align:center;">
        <div style="font-size:11px;color:#5a6b7f;">FPL Tracker · Automated Daily Briefing</div>
        <div style="font-size:10px;color:#3a4555;margin-top:4px;">Predictions are estimates based on statistical modeling. Good luck! 🍀</div>
    </div>

</div>
</body>
</html>"""

    return html


def send_email_smtp(
    html: str,
    to_email: str,
    subject: str = "FPL Tracker — Daily Briefing",
    from_email: str | None = None,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_password: str = "",
) -> bool:
    """
    Send the email report via SMTP.
    
    For Gmail, use an App Password (not your regular password).
    Generate one at: https://myaccount.google.com/apppasswords
    """
    from_addr = from_email or smtp_user

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"FPL Tracker <{from_addr}>"
    msg["To"] = to_email

    # Plain text fallback
    plain = "Your FPL Tracker daily briefing is ready. View in an HTML-compatible email client."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_addr, [to_email], msg.as_string())
        logger.info("Email sent to %s", to_email)
        return True
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False


def send_email_resend(
    html: str,
    to_email: str,
    subject: str = "FPL Tracker — Daily Briefing",
    from_email: str = "FPL Tracker <noreply@yourdomain.com>",
    api_key: str = "",
) -> bool:
    """
    Send the email report via Resend API.
    Sign up at https://resend.com — free tier = 3000 emails/month.
    """
    import urllib.request
    import json as _json

    payload = _json.dumps({
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = _json.loads(resp.read().decode())
            logger.info("Email sent via Resend: %s", result)
            return True
    except Exception as e:
        logger.error("Failed to send via Resend: %s", e)
        return False
