import os
import stripe
import anthropic
import json
import re
import time
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv
import secrets
import PyPDF2
import io

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
YOUR_DOMAIN = os.getenv("YOUR_DOMAIN", "http://localhost:5000")

PRICE_BASIC = 900    # $9.00  — 7 days unlimited audits
PRICE_PRO   = 2900   # $29.00 — lifetime unlimited audits + rewrites

SEVEN_DAYS = 7 * 24 * 60 * 60  # seconds


def extract_text_from_pdf(pdf_bytes):
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return text.strip()
    except Exception:
        return None


def safe_parse_json(raw, client):
    raw = re.sub(r'^```json\s*', '', raw.strip())
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw).strip()

    # 1. Direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass

    # 2. Extract outermost { } block
    try:
        start = raw.index('{')
        depth, end = 0, start
        for i, ch in enumerate(raw[start:], start):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0: end = i; break
        return json.loads(raw[start:end + 1])
    except Exception:
        pass

    # 3. Ask Claude to fix it
    try:
        fix = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content":
                f"Fix any JSON syntax errors below and return ONLY the corrected JSON, nothing else:\n\n{raw}"}],
        )
        fixed = re.sub(r'^```json\s*|^```\s*|\s*```$', '', fix.content[0].text.strip()).strip()
        return json.loads(fixed)
    except Exception:
        return None


def session_is_valid():
    """Check if current session is still within its access window."""
    plan = session.get("plan")
    if not plan:
        return False
    if plan == "pro":
        # Pro = lifetime, never expires
        return session.get("paid") == True
    if plan == "basic":
        # Basic = expires 7 days after purchase
        expires_at = session.get("expires_at", 0)
        return time.time() < expires_at
    return False


def session_can_rewrite():
    return session.get("plan") == "pro" and session.get("paid") == True


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    plan = request.form.get("plan", "basic")

    if plan == "pro":
        price_data = {
            "currency": "usd",
            "unit_amount": PRICE_PRO,
            "product_data": {"name": "ClearHire Pro — Lifetime Unlimited Audits + Rewrites"},
        }
    else:
        price_data = {
            "currency": "usd",
            "unit_amount": PRICE_BASIC,
            "product_data": {"name": "ClearHire Basic — 7-Day Unlimited Audits"},
        }

    cs = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price_data": price_data, "quantity": 1}],
        mode="payment",
        success_url=YOUR_DOMAIN + "/upload?session_id={CHECKOUT_SESSION_ID}&plan=" + plan,
        cancel_url=YOUR_DOMAIN + "/?cancelled=true",
    )
    return redirect(cs.url, code=303)


@app.route("/upload")
def upload_page():
    session_id = request.args.get("session_id")
    plan = request.args.get("plan", "basic")

    # Fresh purchase
    if session_id:
        try:
            cs = stripe.checkout.Session.retrieve(session_id)
            if cs.payment_status not in ("paid",) and cs.status not in ("complete",):
                return redirect(url_for("index"))
            session["paid"] = True
            session["plan"] = plan
            if plan == "basic":
                session["expires_at"] = time.time() + SEVEN_DAYS
            # Pro has no expiry
        except Exception:
            return redirect(url_for("index"))

    # Returning user with active session
    elif session_is_valid():
        plan = session.get("plan")
    else:
        return redirect(url_for("index"))

    # Calculate time remaining for basic plan
    time_remaining = None
    if session.get("plan") == "basic":
        secs_left = session.get("expires_at", 0) - time.time()
        if secs_left > 0:
            days_left = int(secs_left // 86400)
            hours_left = int((secs_left % 86400) // 3600)
            time_remaining = f"{days_left}d {hours_left}h remaining"

    return render_template("upload.html", plan=plan, time_remaining=time_remaining)


@app.route("/analyze", methods=["POST"])
def analyze():
    if not session_is_valid():
        return jsonify({"error": "Your session has expired. Please purchase a new plan to continue."}), 403

    if "resume" not in request.files:
        return jsonify({"error": "No file received."}), 400

    file = request.files["resume"]
    target_role = request.form.get("target_role", "").strip()
    experience_level = request.form.get("experience_level", "mid")
    include_rewrite = request.form.get("include_rewrite", "false") == "true"

    if not file or file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    if not file.filename.lower().endswith((".pdf", ".txt")):
        return jsonify({"error": "Please upload a PDF or TXT file."}), 400

    # Only pro users can request rewrites
    if include_rewrite and not session_can_rewrite():
        include_rewrite = False

    file_bytes = file.read()

    if file.filename.lower().endswith(".pdf"):
        resume_text = extract_text_from_pdf(file_bytes)
        if not resume_text:
            return jsonify({"error": "Could not read your PDF. Try saving as .txt and uploading that instead."}), 400
    else:
        resume_text = file_bytes.decode("utf-8", errors="ignore")

    if len(resume_text.strip()) < 100:
        return jsonify({"error": "Resume appears empty or too short. Please check your file."}), 400

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    rewrite_field = ""
    if include_rewrite:
        rewrite_field = ',\n  "rewritten_resume": "<complete rewritten resume as plain text, ATS-optimized, strong action verbs and metrics>"'

    prompt = f"""You are a senior career consultant and former hiring manager with 15 years of experience reviewing resumes at top companies. Give honest, specific, professional feedback.

Analyze this resume carefully.

TARGET ROLE: {target_role if target_role else "General job market"}
EXPERIENCE LEVEL: {experience_level}

RESUME:
{resume_text[:4000]}

Return ONLY a raw JSON object — no markdown, no explanation, no code fences. Exactly this structure:

{{
  "score": <integer 0-100>,
  "score_label": "<Critical | Needs Work | Average | Good | Strong | Excellent>",
  "summary": "<3 sentences: overall impression, would a recruiter keep reading, one strength and one weakness>",
  "sections": [
    {{
      "name": "Formatting and Presentation",
      "grade": "<A | B | C | D | F>",
      "assessment": "<specific feedback referencing actual content from this resume>",
      "recommendation": "<one concrete action to improve>"
    }},
    {{
      "name": "Work Experience",
      "grade": "<A | B | C | D | F>",
      "assessment": "<specific feedback on bullet points, metrics, action verbs, impact>",
      "recommendation": "<one concrete action>"
    }},
    {{
      "name": "Skills and Keywords",
      "grade": "<A | B | C | D | F>",
      "assessment": "<feedback on ATS keyword optimization and relevance>",
      "recommendation": "<one concrete action>"
    }},
    {{
      "name": "Education and Credentials",
      "grade": "<A | B | C | D | F>",
      "assessment": "<feedback on how education is presented>",
      "recommendation": "<one concrete action>"
    }},
    {{
      "name": "Overall Competitiveness",
      "grade": "<A | B | C | D | F>",
      "assessment": "<would this get an interview at a competitive company, and why>",
      "recommendation": "<the single most important change to make>"
    }}
  ],
  "critical_gaps": [
    "<specific issue 1 referencing actual resume content>",
    "<specific issue 2>",
    "<specific issue 3>"
  ],
  "immediate_actions": [
    "<quick fix they can do today>",
    "<quick fix 2>",
    "<quick fix 3>"
  ],
  "honest_verdict": "<one direct sentence summarizing exactly what this resume is and what must change>"
  {rewrite_field}
}}"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )

    result = safe_parse_json(message.content[0].text, client)

    if result is None:
        return jsonify({"error": "Analysis failed to process. Please try again."}), 500

    # Add metadata so frontend knows what plan they're on
    result["_plan"] = session.get("plan")
    result["_can_rewrite"] = session_can_rewrite()

    return jsonify(result)


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/session-status")
def session_status():
    """Frontend can poll this to check if session is still valid."""
    valid = session_is_valid()
    plan = session.get("plan")
    expires_at = session.get("expires_at", 0)
    secs_left = max(0, expires_at - time.time()) if plan == "basic" else None
    return jsonify({
        "valid": valid,
        "plan": plan,
        "seconds_remaining": secs_left
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n✅  ClearHire is running → http://localhost:{port}\n")
    app.run(debug=False, port=port, host="0.0.0.0")
