"""casper_page — the mascot's face, ear and mouth. One self-contained page.

The browser already has everything a voice companion needs:
  - a microphone + speech recognition (webkitSpeechRecognition)
  - a voice (speechSynthesis)
So the companion needs no app build. This page is Casper: a ghost that
floats, glows when it has something to tell you, listens when you press to
talk, answers from the graded data (via POST /api/act action=say), and
speaks the answer aloud.

Proactive, not nagging: it polls /api/state, and when there is something
worth saying AND you are at a pause (never mid-flow), Casper drifts forward,
brightens, and says it once. The same interruptibility gate as the
notification path — one mind, two mouths.
"""

CASPER_PAGE = r"""<!doctype html><meta charset="utf-8">
<title>Casper</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<body style="margin:0;height:100vh;overflow:hidden;background:radial-gradient(circle at 50% 40%,#141210,#070605 70%);
color:#d8d2c4;font:15px/1.55 -apple-system,Helvetica,sans-serif;display:flex;
flex-direction:column;align-items:center;justify-content:center;gap:6px">

<div id="bubble" style="max-width:560px;min-height:64px;text-align:center;padding:16px 22px;
border-radius:16px;background:rgba(30,27,22,.72);border:1px solid #2a2620;opacity:0;
transition:opacity .5s;font-size:16px;line-height:1.5"></div>

<svg id="ghost" width="200" height="230" viewBox="0 0 100 115" style="cursor:pointer;filter:drop-shadow(0 0 26px rgba(227,177,64,.30))">
  <defs>
    <radialGradient id="gg" cx="38%" cy="30%">
      <stop offset="0%" stop-color="#fff6e0"/><stop offset="55%" stop-color="#E3B140"/>
      <stop offset="100%" stop-color="#7d5a12"/>
    </radialGradient>
  </defs>
  <g id="body">
    <path id="skirt" fill="url(#gg)" opacity=".97"
      d="M50 8c-19 0-31 14-31 33v46c0 5 5 7 8 3l6-7c2-3 6-3 8 0l5 6c2 3 6 3 8 0l5-6c2-3 6-3 8 0l6 7c3 4 8 2 8-3V41C81 22 69 8 50 8z"/>
    <ellipse id="eyeL" cx="39" cy="42" rx="4.6" ry="6" fill="#1a1408"/>
    <ellipse id="eyeR" cx="61" cy="42" rx="4.6" ry="6" fill="#1a1408"/>
    <ellipse id="mouth" cx="50" cy="58" rx="5" ry="3.5" fill="#1a1408" opacity=".85"/>
  </g>
</svg>

<div id="state" style="font-size:12px;color:#6b6557;letter-spacing:.08em;height:18px"></div>
<div style="display:flex;gap:10px;margin-top:4px">
  <button id="talk" class="b">hold to talk</button>
  <button id="ask" class="b">ask about my work</button>
  <a class="b" href="/" style="text-decoration:none">the full picture</a>
</div>
<div id="hint" style="font-size:11px;color:#4a463c;margin-top:10px;max-width:520px;text-align:center"></div>

<style>
.b{cursor:pointer;border:1px solid #2a2620;background:transparent;color:#E3B140;
   border-radius:9px;padding:8px 15px;font-size:13px}
.b:hover{background:#1d1a14}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-11px)}}
@keyframes blink{0%,92%,100%{ry:6}96%{ry:.6}}
#ghost{animation:float 4.6s ease-in-out infinite}
#eyeL,#eyeR{animation:blink 5.5s infinite}
</style>

<script>
const ghost=document.getElementById("ghost"), bubble=document.getElementById("bubble"),
      stateEl=document.getElementById("state"), hint=document.getElementById("hint"),
      mouth=document.getElementById("mouth");
let lastSaid="", speaking=false, listening=false;

function show(text, ms=9000){
  bubble.textContent=text; bubble.style.opacity=1;
  clearTimeout(show._t); show._t=setTimeout(()=>bubble.style.opacity=0, ms);
}
function glow(on){
  ghost.style.filter = on ? "drop-shadow(0 0 46px rgba(227,177,64,.75))"
                          : "drop-shadow(0 0 26px rgba(227,177,64,.30))";
}
function mouthMove(on){
  clearInterval(mouthMove._i);
  if(!on){ mouth.setAttribute("ry","3.5"); return; }
  mouthMove._i=setInterval(()=>{
    mouth.setAttribute("ry", (2 + Math.random()*5).toFixed(1));
  }, 110);
}
function say(text){
  if(!text) return;
  show(text); glow(true); speaking=true; mouthMove(true);
  try{
    speechSynthesis.cancel();
    const u=new SpeechSynthesisUtterance(text);
    u.rate=1.0; u.pitch=1.05;
    const v=speechSynthesis.getVoices().find(v=>/Samantha|Karen|Serena|Google US/.test(v.name));
    if(v) u.voice=v;
    u.onend=()=>{speaking=false; mouthMove(false); glow(false); stateEl.textContent="";};
    speechSynthesis.speak(u);
  }catch(e){ speaking=false; mouthMove(false); glow(false); }
}
async function post(body){
  const r=await fetch("/api/act",{method:"POST",
    headers:{"Content-Type":"application/json","X-Meditate":"1"},
    body:JSON.stringify(body)});
  return r.json();
}
async function askCasper(utterance){
  stateEl.textContent="thinking…";
  try{
    const j=await post({action:"say", value:utterance});
    const t=j.turn||{}; say(j.output||"I'm not sure.");
    if(t.action) hint.textContent="→ "+t.action+(t.executed?" (done)":"  ·  say \"do it\" to run");
  }catch(e){ say("I couldn't reach my own data just now."); }
}

// ---- ear: browser speech recognition, push to talk -------------------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const talk = document.getElementById("talk");
if(SR){
  const rec=new SR(); rec.lang="en-US"; rec.interimResults=true; rec.continuous=false;
  rec.onstart =()=>{listening=true; stateEl.textContent="listening…"; glow(true);};
  rec.onend   =()=>{listening=false; if(!speaking){glow(false);} };
  rec.onerror =(e)=>{stateEl.textContent=""; listening=false; glow(false);
                     if(e.error==="not-allowed") show("I need microphone permission to hear you.");};
  rec.onresult=(e)=>{
    let txt=""; for(const r of e.results) txt+=r[0].transcript;
    stateEl.textContent="“"+txt+"”";
    if(e.results[e.results.length-1].isFinal){ askCasper(txt.trim()); }
  };
  const start=()=>{ if(!listening){ try{rec.start()}catch(_){} } };
  const stop =()=>{ try{rec.stop()}catch(_){} };
  talk.addEventListener("mousedown",start); talk.addEventListener("mouseup",stop);
  talk.addEventListener("touchstart",e=>{e.preventDefault();start()});
  talk.addEventListener("touchend",e=>{e.preventDefault();stop()});
  ghost.addEventListener("click",()=>{ listening?stop():start(); });
}else{
  talk.textContent="type to talk";
  talk.onclick=()=>{ const t=prompt("Say something to Casper:"); if(t) askCasper(t); };
  hint.textContent="This browser has no speech recognition — Chrome or Safari can hear you.";
}
document.getElementById("ask").onclick=()=>askCasper("what should I be looking at");

// ---- proactive: speak once, only at a pause, never mid-flow ----------
async function watch(){
  try{
    const s=await (await fetch("/api/state")).json();
    const st=s.timing||{}, b=s.briefing||{};
    if(b.headline && b.kind!=="clear"){
      glow(true);
      if(st.interrupt_ok && b.headline!==lastSaid && !speaking && !listening){
        lastSaid=b.headline; say(b.headline);
        if(b.action) hint.textContent="→ "+b.action;
      }
    }else{ if(!speaking&&!listening) glow(false); }
  }catch(e){}
}
speechSynthesis.getVoices();
watch(); setInterval(watch, 15000);
</script></body>"""
