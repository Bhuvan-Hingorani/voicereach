# VoiceReach Backend Server - FIXED VERSION
# ==========================================
# Install dependencies:
#   pip install flask twilio groq flask-cors
#
# Run:
#   python server.py
#
# Then in a NEW terminal:
#   ngrok http 5000

from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from groq import Groq
import os

app = Flask(__name__)
CORS(app)

@app.after_request
def add_headers(response):
    # Bypass ngrok browser warning page — this was blocking all fetch() calls
    response.headers['ngrok-skip-browser-warning'] = 'true'
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    return '', 200

# In-memory conversation store
conversations = {}


# ─────────────────────────────────────────
# /make-call  ← Browser calls this
# ─────────────────────────────────────────
@app.route('/make-call', methods=['POST'])
def make_call():
    """
    Browser sends call request here.
    Server calls Twilio on behalf of browser (avoids CORS block).
    """
    data = request.get_json()

    account_sid  = data.get('twilioSid')
    auth_token   = data.get('twilioToken')
    from_number  = data.get('fromPhone')
    to_number    = data.get('toPhone')
    webhook_url  = data.get('webhookUrl')

    print(f"\n📞 Initiating call to: {to_number}")
    print(f"   From: {from_number}")
    print(f"   Webhook: {webhook_url}")

    try:
        client = Client(account_sid, auth_token)
        call = client.calls.create(
            to=to_number,
            from_=from_number,
            url=webhook_url,
            method='GET',
            status_callback=webhook_url.split('/voice')[0] + '/status',
            status_callback_method='POST',
            machine_detection='Enable',
            timeout=30
        )
        print(f"   ✅ Call SID: {call.sid}")
        return jsonify({'success': True, 'sid': call.sid})

    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 400


# ─────────────────────────────────────────
# /voice  ← Twilio calls this when call connects
# ─────────────────────────────────────────
@app.route('/voice', methods=['GET', 'POST'])
def voice():
    """Twilio hits this when the call is picked up — plays the intro."""
    contact_name = request.values.get('contact_name', 'there')
    company      = request.values.get('company', 'our company')
    agent_name   = request.values.get('agent_name', 'Alex')
    product      = request.values.get('product', 'our service')
    goal         = request.values.get('goal', 'schedule_demo')
    voice        = request.values.get('voice', 'Polly.Joanna')
    groq_key     = request.values.get('groq_key', '')
    call_sid     = request.values.get('CallSid', 'unknown')

    # Save context for this call
    conversations[call_sid] = {
        'history':  [],
        'contact':  contact_name,
        'company':  company,
        'agent':    agent_name,
        'product':  product,
        'goal':     goal,
        'groq_key': groq_key,
        'voice':    voice,
    }

    goal_lines = {
        'schedule_demo': 'I would love to schedule a quick 15-minute demo. Would that work for you?',
        'callback':      'Could I arrange a better time to call you back?',
        'interest_check':'Would you be open to hearing a bit more about it?',
        'direct_close':  'We have a special offer right now. Would you like to get started today?',
    }

    greeting = (
        f"Hello, am I speaking with {contact_name}? "
        f"Hi {contact_name}, I'm {agent_name} calling from {company}. "
        f"I hope I'm not catching you at a bad time. "
        f"We help businesses with {product}. "
        f"{goal_lines.get(goal, '')}"
    )

    # Build TwiML
    base_url   = request.url_root.rstrip('/')
    action_url = f"{base_url}/respond?call_sid={call_sid}&groq_key={groq_key}&voice={voice}&company={company}&agent_name={agent_name}&product={product}&goal={goal}&contact_name={contact_name}"

    response = VoiceResponse()
    gather   = Gather(
        input='speech',
        action=action_url,
        method='POST',
        speech_timeout='auto',
        language='en-IN'
    )
    gather.say(greeting, voice=voice)
    response.append(gather)
    response.say("I didn't catch that. I'll try again later. Have a great day!", voice=voice)
    response.hangup()

    return Response(str(response), mimetype='text/xml')


# ─────────────────────────────────────────
# /respond  ← Twilio calls this after contact speaks
# ─────────────────────────────────────────
@app.route('/respond', methods=['POST'])
def respond():
    """Handles each reply from the contact using Groq AI."""
    call_sid      = request.values.get('call_sid', request.values.get('CallSid', ''))
    speech_result = request.values.get('SpeechResult', '')
    groq_key      = request.values.get('groq_key', '')
    voice         = request.values.get('voice', 'Polly.Joanna')
    company       = request.values.get('company', 'our company')
    agent_name    = request.values.get('agent_name', 'Alex')
    product       = request.values.get('product', 'our service')
    goal          = request.values.get('goal', 'schedule_demo')
    contact_name  = request.values.get('contact_name', 'there')

    print(f"\n🎙️  Contact said: {speech_result}")

    ctx = conversations.get(call_sid)
    if not ctx:
        # Rebuild context if lost
        ctx = {
            'history':  [],
            'contact':  contact_name,
            'company':  company,
            'agent':    agent_name,
            'product':  product,
            'goal':     goal,
            'groq_key': groq_key,
            'voice':    voice,
        }
        conversations[call_sid] = ctx

    ctx['history'].append({"role": "user", "content": speech_result})

    goal_map = {
        'schedule_demo': 'schedule a quick 15-minute demo',
        'callback':      'arrange a convenient callback time',
        'interest_check':'gauge their interest level',
        'direct_close':  'close the sale directly',
    }

    system_prompt = f"""You are {ctx['agent']}, a professional and friendly sales agent from {ctx['company']}.
You called {ctx['contact']} to pitch: {ctx['product']}.
Your goal on this call: {goal_map.get(ctx['goal'], 'schedule a demo')}.

STRICT RULES:
- Keep replies to 2-3 short sentences MAX (this is a phone call, not an essay)
- Be warm, natural, and conversational
- Handle objections politely and confidently
- If they agree to a demo/meeting, confirm it and say a warm goodbye
- If they are clearly not interested, thank them genuinely and end the call
- Never be pushy, robotic, or repeat the same line twice
- Never use bullet points or lists in your reply"""

    try:
        client   = Groq(api_key=ctx['groq_key'] or groq_key)
        chat     = client.chat.completions.create(
            model='llama3-8b-8192',
            messages=[{"role": "system", "content": system_prompt}] + ctx['history'],
            max_tokens=120,
            temperature=0.75
        )
        ai_reply = chat.choices[0].message.content.strip()
    except Exception as e:
        print(f"   ❌ Groq error: {e}")
        ai_reply = "I apologize, I'm having a small technical issue. I'll have a colleague reach out to you shortly. Thank you so much for your time!"

    ctx['history'].append({"role": "assistant", "content": ai_reply})
    print(f"🤖  Agent reply: {ai_reply}")

    # Decide whether to end the call
    end_phrases = ['goodbye', 'bye', 'take care', 'have a great', 'not interested',
                   'thank you for your time', 'we\'ll be in touch', 'confirmed']
    should_end  = (
        any(p in ai_reply.lower() for p in end_phrases) or
        len(ctx['history']) > 14  # max 7 turns
    )

    base_url   = request.url_root.rstrip('/')
    action_url = (
        f"{base_url}/respond"
        f"?call_sid={call_sid}"
        f"&groq_key={groq_key}"
        f"&voice={voice}"
        f"&company={company}"
        f"&agent_name={agent_name}"
        f"&product={product}"
        f"&goal={goal}"
        f"&contact_name={contact_name}"
    )

    response = VoiceResponse()
    if should_end:
        response.say(ai_reply, voice=ctx['voice'])
        response.hangup()
    else:
        gather = Gather(
            input='speech',
            action=action_url,
            method='POST',
            speech_timeout='auto',
            language='en-IN'
        )
        gather.say(ai_reply, voice=ctx['voice'])
        response.append(gather)
        response.say("Thank you for your time. Have a wonderful day!", voice=ctx['voice'])
        response.hangup()

    return Response(str(response), mimetype='text/xml')


# ─────────────────────────────────────────
# /status  ← Twilio posts call status updates
# ─────────────────────────────────────────
@app.route('/status', methods=['POST'])
def status():
    call_status = request.values.get('CallStatus', 'unknown')
    call_sid    = request.values.get('CallSid', '')
    duration    = request.values.get('CallDuration', '0')
    print(f"\n📊 Call {call_sid[:12]}... → {call_status} (duration: {duration}s)")
    return '', 200


# ─────────────────────────────────────────
# /health  ← Quick check that server is alive
# ─────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'VoiceReach server is running!'})


# ─────────────────────────────────────────
# /test-groq  ← Diagnostic: test Groq key
# ─────────────────────────────────────────
@app.route('/test-groq', methods=['POST'])
def test_groq():
    data     = request.get_json()
    groq_key = data.get('groq_key', '')
    try:
        client = Groq(api_key=groq_key)
        chat   = client.chat.completions.create(
            model='llama3-8b-8192',
            messages=[{'role': 'user', 'content': 'Reply with exactly: Groq is working!'}],
            max_tokens=20
        )
        reply = chat.choices[0].message.content.strip()
        return jsonify({'success': True, 'reply': reply})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ─────────────────────────────────────────
# /test-twilio  ← Diagnostic: test Twilio creds
# ─────────────────────────────────────────
@app.route('/test-twilio', methods=['POST'])
def test_twilio():
    data     = request.get_json()
    sid      = data.get('twilioSid', '')
    token    = data.get('twilioToken', '')
    from_num = data.get('fromPhone', '')
    try:
        client  = Client(sid, token)
        account = client.api.accounts(sid).fetch()
        # Check if from number belongs to account
        numbers = client.incoming_phone_numbers.list(phone_number=from_num)
        return jsonify({
            'success':      True,
            'accountName':  account.friendly_name,
            'phoneValid':   len(numbers) > 0
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ─────────────────────────────────────────
# /test-verified  ← Diagnostic: check if number is verified
# ─────────────────────────────────────────
@app.route('/test-verified', methods=['POST'])
def test_verified():
    data    = request.get_json()
    sid     = data.get('twilioSid', '')
    token   = data.get('twilioToken', '')
    to_num  = data.get('toPhone', '')
    try:
        client   = Client(sid, token)
        verified = client.outgoing_caller_ids.list(phone_number=to_num)
        return jsonify({'verified': len(verified) > 0})
    except Exception as e:
        return jsonify({'verified': False, 'error': str(e)}), 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"""
╔══════════════════════════════════════╗
║   VoiceReach Server — RUNNING ✅    ║
║   Port: {port}                          ║
║   Health: http://localhost:{port}/health║
╚══════════════════════════════════════╝
""")
    app.run(host='0.0.0.0', port=port, debug=True)