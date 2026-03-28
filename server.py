# VoiceReach Backend Server - FINAL VERSION FOR RENDER.COM
# =========================================================
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from groq import Groq
from urllib.parse import quote
import os, threading, time

app = Flask(__name__)
CORS(app)

@app.after_request
def add_headers(response):
    response.headers['ngrok-skip-browser-warning'] = 'true'
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    return '', 200

conversations = {}

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'VoiceReach server is running!'})

@app.route('/make-call', methods=['POST'])
def make_call():
    data        = request.get_json()
    account_sid = data.get('twilioSid', '').strip()
    auth_token  = data.get('twilioToken', '').strip()
    from_number = data.get('fromPhone', '').strip()
    to_number   = data.get('toPhone', '').strip()
    webhook_url = data.get('webhookUrl', '').strip()

    print(f"\n📞 Calling: {to_number} from {from_number}")

    if not account_sid or not account_sid.startswith('AC'):
        return jsonify({'success': False, 'error': 'Invalid Twilio Account SID — must start with AC'}), 400
    if not auth_token:
        return jsonify({'success': False, 'error': 'Twilio Auth Token is missing'}), 400
    if not from_number.startswith('+'):
        return jsonify({'success': False, 'error': 'From phone must include country code e.g. +14155552671'}), 400
    if not to_number.startswith('+'):
        return jsonify({'success': False, 'error': 'To phone must include country code e.g. +919876543210'}), 400

    try:
        client = Client(account_sid, auth_token)
        call   = client.calls.create(
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
        err = str(e)
        print(f"   ❌ Error: {err}")
        if '21608' in err:
            msg = f'{to_number} is not verified in Twilio trial. Go to console.twilio.com > Verified Caller IDs and add it.'
        elif '20003' in err or 'authenticate' in err.lower():
            msg = 'Wrong Twilio credentials — check your Account SID and Auth Token.'
        elif '21211' in err:
            msg = f'Invalid phone number: {to_number}. Use format: +919876543210'
        else:
            msg = err
        return jsonify({'success': False, 'error': msg}), 400

@app.route('/voice', methods=['GET', 'POST'])
def voice():
    contact_name = request.values.get('contact_name', 'there')
    company      = request.values.get('company', 'our company')
    agent_name   = request.values.get('agent_name', 'Alex')
    product      = request.values.get('product', 'our service')
    goal         = request.values.get('goal', 'schedule_demo')
    tts_voice    = request.values.get('voice', 'Polly.Joanna')
    groq_key     = request.values.get('groq_key', '')
    call_sid     = request.values.get('CallSid', 'unknown')

    print(f"\n📲 Call connected — {contact_name} | {company} | {agent_name}")

    conversations[call_sid] = {
        'history': [], 'contact': contact_name, 'company': company,
        'agent': agent_name, 'product': product, 'goal': goal,
        'groq_key': groq_key, 'voice': tts_voice,
    }

    goal_lines = {
        'schedule_demo': 'I would love to schedule a quick 15-minute demo. Would that work for you?',
        'callback':      'Could I arrange a better time to call you back?',
        'interest_check':'Would you be open to hearing a bit more?',
        'direct_close':  'We have a special offer right now. Would you like to get started today?',
    }

    greeting = (
        f"Hello, am I speaking with {contact_name}? "
        f"Hi {contact_name}, I am {agent_name} calling from {company}. "
        f"I hope I have not caught you at a bad time. "
        f"We help businesses with {product}. "
        f"{goal_lines.get(goal, 'Do you have a couple of minutes?')}"
    )

    base_url   = request.url_root.rstrip('/')
    action_url = (f"{base_url}/respond?call_sid={call_sid}"
                  f"&groq_key={quote(groq_key)}&voice={tts_voice}"
                  f"&company={quote(company)}&agent_name={quote(agent_name)}"
                  f"&product={quote(product)}&goal={goal}&contact_name={quote(contact_name)}")

    resp   = VoiceResponse()
    gather = Gather(input='speech', action=action_url, method='POST', speech_timeout='auto', language='en-IN')
    gather.say(greeting, voice=tts_voice)
    resp.append(gather)
    resp.say("I did not catch that. I will try again later. Have a great day!", voice=tts_voice)
    resp.hangup()
    return Response(str(resp), mimetype='text/xml')

@app.route('/respond', methods=['POST'])
def respond():
    call_sid     = request.values.get('call_sid', request.values.get('CallSid', ''))
    speech       = request.values.get('SpeechResult', '')
    groq_key     = request.values.get('groq_key', '')
    tts_voice    = request.values.get('voice', 'Polly.Joanna')
    company      = request.values.get('company', 'our company')
    agent_name   = request.values.get('agent_name', 'Alex')
    product      = request.values.get('product', 'our service')
    goal         = request.values.get('goal', 'schedule_demo')
    contact_name = request.values.get('contact_name', 'there')

    print(f"\n🎙️  [{contact_name}] said: {speech}")

    ctx = conversations.get(call_sid, {
        'history': [], 'contact': contact_name, 'company': company,
        'agent': agent_name, 'product': product, 'goal': goal,
        'groq_key': groq_key, 'voice': tts_voice,
    })
    conversations[call_sid] = ctx
    ctx['history'].append({"role": "user", "content": speech})

    goal_map = {
        'schedule_demo': 'schedule a quick 15-minute demo call',
        'callback':      'arrange a convenient time for a callback',
        'interest_check':'understand if they are interested',
        'direct_close':  'close the sale directly on this call',
    }

    system_prompt = f"""You are {ctx['agent']}, a friendly professional sales agent from {ctx['company']}.
You called {ctx['contact']} about: {ctx['product']}.
Goal: {goal_map.get(ctx['goal'], 'schedule a demo')}.
Rules: Max 2-3 short sentences. Sound natural. Handle objections politely.
If they agree, confirm and say goodbye. If not interested after 2 tries, thank them and end."""

    try:
        client   = Groq(api_key=ctx['groq_key'] or groq_key)
        chat     = client.chat.completions.create(
            model='llama3-8b-8192',
            messages=[{"role": "system", "content": system_prompt}] + ctx['history'],
            max_tokens=120, temperature=0.75
        )
        ai_reply = chat.choices[0].message.content.strip()
    except Exception as e:
        print(f"   ❌ Groq error: {e}")
        ai_reply = "I apologize for the technical issue. Our team will reach out to you shortly. Thank you!"

    ctx['history'].append({"role": "assistant", "content": ai_reply})
    print(f"🤖  Agent: {ai_reply}")

    end_phrases = ['goodbye', 'bye', 'take care', 'have a great', 'have a wonderful',
                   'not interested', 'thank you for your time', 'confirmed', 'talk soon', 'reach out shortly']
    should_end  = any(p in ai_reply.lower() for p in end_phrases) or len(ctx['history']) > 14

    base_url   = request.url_root.rstrip('/')
    action_url = (f"{base_url}/respond?call_sid={call_sid}"
                  f"&groq_key={quote(groq_key)}&voice={tts_voice}"
                  f"&company={quote(company)}&agent_name={quote(agent_name)}"
                  f"&product={quote(product)}&goal={goal}&contact_name={quote(contact_name)}")

    resp = VoiceResponse()
    if should_end:
        resp.say(ai_reply, voice=ctx['voice'])
        resp.hangup()
    else:
        gather = Gather(input='speech', action=action_url, method='POST', speech_timeout='auto', language='en-IN')
        gather.say(ai_reply, voice=ctx['voice'])
        resp.append(gather)
        resp.say("Thank you for your time. Have a wonderful day!", voice=ctx['voice'])
        resp.hangup()
    return Response(str(resp), mimetype='text/xml')

@app.route('/status', methods=['POST'])
def status():
    print(f"\n📊 {request.values.get('CallStatus')} | SID: {request.values.get('CallSid','')[:14]}... | {request.values.get('CallDuration','0')}s")
    return '', 200

@app.route('/test-groq', methods=['POST'])
def test_groq():
    groq_key = request.get_json().get('groq_key', '')
    try:
        client = Groq(api_key=groq_key)
        chat   = client.chat.completions.create(
            model='llama3-8b-8192',
            messages=[{'role': 'user', 'content': 'Say exactly: Groq API is working'}],
            max_tokens=20
        )
        return jsonify({'success': True, 'reply': chat.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/test-twilio', methods=['POST'])
def test_twilio():
    d = request.get_json()
    try:
        client  = Client(d.get('twilioSid',''), d.get('twilioToken',''))
        account = client.api.accounts(d.get('twilioSid','')).fetch()
        numbers = client.incoming_phone_numbers.list(phone_number=d.get('fromPhone',''))
        return jsonify({'success': True, 'accountName': account.friendly_name, 'phoneValid': len(numbers) > 0})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/test-verified', methods=['POST'])
def test_verified():
    d = request.get_json()
    try:
        client   = Client(d.get('twilioSid',''), d.get('twilioToken',''))
        verified = client.outgoing_caller_ids.list(phone_number=d.get('toPhone',''))
        return jsonify({'verified': len(verified) > 0})
    except Exception as e:
        return jsonify({'verified': False, 'error': str(e)}), 400

# Keep Render free tier awake
def keep_alive():
    time.sleep(60)
    render_url = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not render_url:
        return
    print(f"💓 Keep-alive started for {render_url}")
    while True:
        try:
            import urllib.request
            urllib.request.urlopen(f"{render_url}/health", timeout=10)
            print("💓 Keep-alive ping OK")
        except:
            pass
        time.sleep(600)

threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n╔══════════════════════════════════╗\n║  VoiceReach Server RUNNING ✅   ║\n║  Port: {port}  Health: /health    ║\n╚══════════════════════════════════╝\n")
    app.run(host='0.0.0.0', port=port, debug=False)