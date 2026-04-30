"use client";
import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  Brain, Zap, Network, Layers, Sparkles, Code2, Wallet, Link2, Cloud,
  Check, X, MessageCircle, ArrowRight, Menu, Bot,
  Coins, Terminal, Headphones, Star, Lock, Database, Cpu, Activity,
  ChevronRight, Shield, Boxes, Download, ChevronDown, PlayCircle, Globe
} from "lucide-react";

/* ─────────────────── GLOBAL STYLES ─────────────────── */
const GLOBAL_CSS = `
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
*{font-family:'Inter',sans-serif;box-sizing:border-box;}
.font-mono{font-family:'JetBrains Mono',monospace;}
html{scroll-behavior:smooth;}
::-webkit-scrollbar{width:8px;}
::-webkit-scrollbar-track{background:#080812;}
::-webkit-scrollbar-thumb{background:#6C63FF;border-radius:4px;}
::-webkit-scrollbar-thumb:hover{background:#7B68EE;}
scrollbar-color:#6C63FF #080812;

@keyframes pulse-glow{0%,100%{opacity:.6;transform:scale(1);}50%{opacity:1;transform:scale(1.05);}}
@keyframes float{0%,100%{transform:translateY(0);}50%{transform:translateY(-10px);}}
@keyframes shimmer{0%{background-position:-200% center;}100%{background-position:200% center;}}
@keyframes flow{0%{transform:translateX(-100%);opacity:0;}20%{opacity:1;}80%{opacity:1;}100%{transform:translateX(100%);opacity:0;}}
@keyframes spin-slow{from{transform:rotate(0deg);}to{transform:rotate(360deg);}}
@keyframes synaptic{0%,100%{opacity:.15;}50%{opacity:1;}}
@keyframes slideUp{from{transform:translateY(80px);opacity:0;}to{transform:translateY(0);opacity:1;}}
@keyframes fadeIn{from{opacity:0;}to{opacity:1;}}
@keyframes connectorPulse{0%,100%{opacity:.25;}50%{opacity:.8;}}
@keyframes accordionIn{from{opacity:0;transform:translateY(-6px);}to{opacity:1;transform:translateY(0);}}

.animate-pulse-glow{animation:pulse-glow 3s ease-in-out infinite;}
.animate-float{animation:float 4s ease-in-out infinite;}
.animate-spin-slow{animation:spin-slow 40s linear infinite;}
.animate-slide-up{animation:slideUp .6s cubic-bezier(.16,1,.3,1) both;}
.animate-fade-in{animation:fadeIn .5s ease both;}

.glow-indigo{box-shadow:0 0 40px rgba(108,99,255,.4),0 0 80px rgba(108,99,255,.2);}
.glow-teal{box-shadow:0 0 40px rgba(0,229,209,.4),0 0 80px rgba(0,229,209,.2);}
.glow-amber{box-shadow:0 0 40px rgba(255,179,71,.5),0 0 80px rgba(255,179,71,.25);}

.text-shimmer{background:linear-gradient(90deg,#fff 0%,#7B68EE 25%,#00E5D1 50%,#FFB347 75%,#fff 100%);background-size:200% auto;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:shimmer 6s linear infinite;}
.grid-bg{background-image:linear-gradient(rgba(108,99,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(108,99,255,.05) 1px,transparent 1px);background-size:60px 60px;}

/* Tooltip */
.tip-wrap{position:relative;display:inline-flex;align-items:center;justify-content:center;cursor:help;}
.tip-box{position:absolute;bottom:calc(100% + 10px);left:50%;transform:translateX(-50%);background:#1a1a2e;border:1px solid rgba(255,179,71,.3);border-radius:10px;padding:8px 12px;font-size:11px;line-height:1.5;color:#c0c0d0;white-space:nowrap;max-width:240px;white-space:normal;text-align:left;pointer-events:none;opacity:0;transition:opacity .2s;z-index:999;box-shadow:0 8px 30px rgba(0,0,0,.6);}
.tip-box::after{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%);border:5px solid transparent;border-top-color:#1a1a2e;}
.tip-wrap:hover .tip-box{opacity:1;}

/* FAQ accordion */
.faq-body{overflow:hidden;transition:max-height .4s cubic-bezier(.16,1,.3,1),opacity .3s ease;}
`;

/* ─────────────────── SHARED PRIMITIVES ─────────────────── */
function GlowButton({ children, className = "", onClick }: any) {
  const defaultOnClick = () => window.open('https://api.nexmem.ai/auth/signup', '_blank');
  const handleClick = onClick || defaultOnClick;
  return (
    <button onClick={handleClick}
      className={`group relative px-7 py-4 rounded-full font-medium overflow-hidden ${className}`}>
      <div className="absolute inset-0 bg-gradient-to-r from-[#6C63FF] to-[#7B68EE]" />
      <div className="absolute inset-0 bg-gradient-to-r from-[#6C63FF] to-[#7B68EE] blur-xl opacity-50 group-hover:opacity-90 transition" />
      <span className="relative flex items-center justify-center gap-2">{children}</span>
    </button>
  );
}
function GhostButton({ children, className = "", onClick }: any) {
  const defaultOnClick = () => window.open('https://api.nexmem.ai/auth/signup', '_blank');
  const handleClick = onClick || defaultOnClick;
  return (
    <button onClick={handleClick}
      className={`px-7 py-4 rounded-full font-medium border border-white/20 hover:bg-white/5 hover:border-white/40 transition flex items-center justify-center gap-2 ${className}`}>
      {children}
    </button>
  );
}

/* ─────────────────── ROOT ─────────────────── */
export default function NexmemLanding() {
  const [scrolled, setScrolled] = useState(false);
  const [heroVisible, setHeroVisible] = useState(true);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [mousePos, setMousePos] = useState({ x: .5, y: .5 });
  const heroRef = useRef(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  /* hero intersection observer for sticky bar */
  useEffect(() => {
    if (!heroRef.current) return;
    const obs = new IntersectionObserver(
      ([e]) => setHeroVisible(e.isIntersecting),
      { threshold: 0.05 }
    );
    obs.observe(heroRef.current);
    return () => obs.disconnect();
  }, []);

  return (
    <div className="bg-[#080812] text-white min-h-screen overflow-x-hidden">
      

      {/* NAV */}
      <nav className={`fixed top-0 left-0 right-0 z-50 transition-all duration-500 ${scrolled ? "bg-[#080812]/80 backdrop-blur-xl border-b border-white/5" : "bg-transparent"}`}>
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <NexmemNode />
            <span className="text-xl font-bold tracking-tight">Nexmem</span>
          </div>
          <div className="hidden md:flex items-center gap-8 text-sm text-gray-300">
            {["Docs","Pricing","Blog"].map(l => <a key={l} href={l === "Pricing" ? "#pricing" : "#"} className="hover:text-white transition">{l}</a>)}
            <a href="#" className="hover:text-white transition flex items-center gap-1.5"><Globe size={14}/>GitHub</a>
          </div>
          <div className="hidden md:block">
            <GlowButton className="text-sm py-2.5 px-5">Get Free API Key <ArrowRight size={14}/></GlowButton>
          </div>
          <button className="md:hidden" onClick={() => setMobileMenu(!mobileMenu)}><Menu size={24}/></button>
        </div>
        {mobileMenu && (
          <div className="md:hidden bg-[#080812]/95 backdrop-blur-xl border-t border-white/5 px-6 py-4 space-y-3">
            {["Docs","Pricing","GitHub","Blog"].map(l => <a key={l} href="#" className="block text-gray-300">{l}</a>)}
            <GlowButton className="w-full text-sm">Get Free API Key</GlowButton>
          </div>
        )}
      </nav>

      <div ref={heroRef}>
        <HeroSection mousePos={mousePos} setMousePos={setMousePos} />
      </div>
      <WorksWith />
      <StatsStrip />
      <HowItWorks />
      <ProblemSolution />
      <MemoryTypes />
      <EngramSection />
      <Testimonials />
      <APISection />
      <Web3Section />
      <Comparison />
      <UseCases />
      <Pricing />
      <FAQ />
      <FinalCTA />
      <Footer />

      {/* STICKY BAR — Change 11 */}
      <StickyBar heroVisible={heroVisible} />
    </div>
  );
}

/* ─────────────────── SHARED NODE ICON ─────────────────── */
function NexmemNode({ size = 7 }: any) {
  return (
    <div className={`relative w-${size} h-${size}`}>
      <div className="absolute inset-0 rounded-full bg-gradient-to-br from-[#6C63FF] to-[#00E5D1] animate-pulse-glow" />
      <div className="absolute inset-1 rounded-full bg-[#080812]" />
      <div className="absolute inset-2 rounded-full bg-gradient-to-br from-[#7B68EE] to-[#00FFC8]" />
    </div>
  );
}

/* ─────────────────── STICKY BAR ─────────────────── */
function StickyBar({ heroVisible }: any) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setTimeout(() => setMounted(true), 1000); }, []);
  const show = mounted && !heroVisible;
  return (
    <div style={{
      position: "fixed", bottom: 24, left: "50%", transform: `translateX(-50%) translateY(${show ? 0 : "100px"})`,
      opacity: show ? 1 : 0, transition: "all .6s cubic-bezier(.16,1,.3,1)",
      zIndex: 40, pointerEvents: show ? "auto" : "none",
      display: "flex", alignItems: "center", gap: 16,
      padding: "12px 20px 12px 16px",
      background: "rgba(10,10,26,.85)",
      backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)",
      border: "1px solid rgba(108,99,255,.35)",
      borderRadius: 999,
      boxShadow: "0 0 40px rgba(108,99,255,.25), 0 8px 32px rgba(0,0,0,.6)",
    }}>
      <NexmemNode size={6} />
      <span style={{ fontSize: 13, color: "#d0d0e8", fontWeight: 500 }}>Nexmem</span>
      <div style={{ width: 1, height: 18, background: "rgba(255,255,255,.12)" }} />
      <button onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "6px 16px", borderRadius: 999, fontSize: 13, fontWeight: 600,
          background: "linear-gradient(90deg,#6C63FF,#7B68EE)", border: "none",
          color: "#fff", cursor: "pointer",
        }}>
        Get Free API Key <ArrowRight size={13} />
      </button>
    </div>
  );
}

/* ─────────────────── HERO ─────────────────── */
function HeroSection({ mousePos, setMousePos }: any) {
  const [loaded, setLoaded] = useState(false);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => { setTimeout(() => setLoaded(true), 100); }, []);
  const handleMouse = (e: React.MouseEvent<HTMLElement>) => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setMousePos({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
  };
  const w1 = ["Your","AI","agent"], w2 = ["finally","remembers."];
  const fade = (d: number) => ({ transition: `all .7s ease ${d}ms`, opacity: loaded ? 1 : 0, transform: loaded ? "translateY(0)" : "translateY(28px)" });
  return (
    <section ref={ref} onMouseMove={handleMouse}
      className="relative min-h-screen flex items-center justify-center pt-24 pb-16 overflow-hidden"
      style={{ background: "radial-gradient(ellipse at center,#13132a 0%,#0A0A1A 50%,#080812 100%)" }}>
      <Constellation mousePos={mousePos} />
      <div className="relative z-10 max-w-5xl mx-auto px-6 text-center">
        {/* Badge — CHANGE 1a */}
        <div style={fade(0)} className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white/5 border border-white/10 backdrop-blur text-xs text-gray-300 mb-8">
          <span className="w-1.5 h-1.5 rounded-full bg-[#00E5D1] animate-pulse" />
          Early access open · 1,000 free writes/month
        </div>

        <h1 className="text-5xl md:text-7xl lg:text-8xl font-bold tracking-tight leading-[1.05] mb-5">
          <div className="flex justify-center gap-3 md:gap-5 flex-wrap">
            {w1.map((w,i) => <span key={i} className="inline-block" style={fade(200+i*120)}>{w}</span>)}
          </div>
          <div className="flex justify-center gap-3 md:gap-5 flex-wrap mt-1">
            {w2.map((w,i) => <span key={i} className={`inline-block ${i===1?"text-shimmer":""}`} style={fade(600+i*120)}>{w}</span>)}
          </div>
        </h1>

        {/* Sub-headline hierarchy — CHANGE 1b */}
        <div style={fade(900)} className="mb-10">
          <p className="text-xl md:text-2xl text-white font-semibold mb-2">
            5 memory types. 1 API. Your agent never forgets.
          </p>
          <p className="text-sm text-gray-500 max-w-xl mx-auto">
            Persistent, compressed, user-owned memory for AI agents — with engram NLP compression, graph recall, and optional Web3 anchoring.
          </p>
        </div>

        <div style={fade(1050)} className="flex flex-col sm:flex-row gap-4 justify-center mb-5">
          <GlowButton>Get Free API Key <ArrowRight size={16} className="group-hover:translate-x-1 transition"/></GlowButton>
          <GhostButton>View Docs <Code2 size={16}/></GhostButton>
        </div>

        {/* Third CTA — CHANGE 1c */}
        <div style={fade(1180)} className="flex justify-center mb-8">
          <a href="#demo" className="inline-flex items-center gap-2 text-sm text-gray-400 hover:text-white transition group">
            <span style={{
              display:"inline-flex", alignItems:"center", justifyContent:"center",
              width:28, height:28, borderRadius:"50%",
              border:"1px solid rgba(255,255,255,.2)",
              background:"rgba(108,99,255,.15)",
              transition:"all .2s",
            }} className="group-hover:border-[rgba(108,99,255,.6)]">
              <span style={{fontSize:10}}>▶</span>
            </span>
            Watch 60-second demo
          </a>
        </div>

        {/* Social proof — CHANGE 1d */}
        <div style={fade(1300)} className="text-xs text-gray-600">
          Built for developers using LangChain, Telegram, and DeFi agents
        </div>
      </div>
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10">
        <div className="w-6 h-10 rounded-full border border-white/20 flex items-start justify-center p-1.5">
          <div className="w-1 h-2 rounded-full bg-white/60 animate-bounce" />
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── CONSTELLATION ─────────────────── */
function Constellation({ mousePos }: any) {
  const memTypes = [
    { color:"#A0C8FF", angle:0 },{ color:"#00E5D1", angle:72 },
    { color:"#7B68EE", angle:144 },{ color:"#7CFC9B", angle:216 },{ color:"#FFB347", angle:288 },
  ];
  const particles = useMemo(() => Array.from({length:120}).map((_,i) => ({
    id:i, x:50+(Math.random()-.5)*100, y:50+(Math.random()-.5)*100,
    r:Math.random()*.18+.04, delay:Math.random()*5, dur:Math.random()*6+4,
    color:["#6C63FF","#00E5D1","#7B68EE","#A0C8FF","#FFB347"][i%5],
  })),[]);
  const edges = useMemo(() => Array.from({length:40}).map((_,i) => ({
    x1:50+(Math.random()-.5)*80, y1:50+(Math.random()-.5)*80,
    x2:50+(Math.random()-.5)*80, y2:50+(Math.random()-.5)*80,
    delay:Math.random()*6,
  })),[]);
  const ox = (mousePos.x-.5)*30, oy = (mousePos.y-.5)*30;
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <svg viewBox="0 0 100 100" className="w-full h-full max-w-[1200px] absolute" preserveAspectRatio="xMidYMid slice"
        style={{transform:`translate(${ox*.3}px,${oy*.3}px)`}}>
        {edges.map((e,i) => <line key={i} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
          stroke="#6C63FF" strokeWidth=".05" opacity=".3"
          style={{animation:`synaptic ${5+Math.random()*4}s ease-in-out ${e.delay}s infinite`}}/>)}
        {particles.map(p => <circle key={p.id} cx={p.x} cy={p.y} r={p.r} fill={p.color} opacity=".7"
          style={{animation:`synaptic ${p.dur}s ease-in-out ${p.delay}s infinite`}}/>)}
      </svg>
      <div className="relative" style={{transform:`translate(${ox}px,${oy}px)`}}>
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-64 h-64 rounded-full"
          style={{background:"radial-gradient(circle,rgba(123,104,238,.3) 0%,rgba(108,99,255,.1) 40%,transparent 70%)",animation:"pulse-glow 4s ease-in-out infinite"}}/>
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-32 h-32 rounded-full"
          style={{background:"radial-gradient(circle,rgba(255,179,71,.4) 0%,rgba(123,104,238,.2) 50%,transparent 80%)",animation:"pulse-glow 3s ease-in-out infinite reverse"}}/>
        <div className="relative w-8 h-8 rounded-full bg-white"
          style={{boxShadow:"0 0 30px #fff,0 0 60px #7B68EE,0 0 90px #6C63FF"}}/>
        <div className="absolute left-1/2 top-1/2 w-0 h-0">
          {memTypes.map((m,i) => (
            <div key={i} className="absolute" style={{animation:"spin-slow 60s linear infinite",animationDelay:`${i*.3}s`}}>
              <div className="absolute" style={{transform:`rotate(${m.angle}deg) translateX(180px)`}}>
                <div className="relative -translate-x-1/2 -translate-y-1/2">
                  <div className="w-4 h-4 rounded-full"
                    style={{background:m.color,boxShadow:`0 0 20px ${m.color},0 0 40px ${m.color}80`,
                      animation:`pulse-glow ${2+i*.3}s ease-in-out infinite`}}/>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────── WORKS WITH — NEW SECTION ─────────────────── */
function WorksWith() {
  const tools = ["LangChain","AutoGen","CrewAI","Telegram Bots","LlamaIndex"];
  return (
    <section className="relative py-8 px-6">
      <div className="max-w-4xl mx-auto flex flex-col sm:flex-row items-center justify-center gap-4">
        <span style={{fontSize:11,color:"#505070",textTransform:"uppercase",letterSpacing:"0.1em",whiteSpace:"nowrap"}}>Works with</span>
        <div style={{width:1,height:16,background:"rgba(255,255,255,.1)"}} className="hidden sm:block"/>
        <div className="flex flex-wrap gap-2 justify-center">
          {tools.map(t => (
            <span key={t} style={{
              padding:"5px 14px", borderRadius:999,
              background:"rgba(255,255,255,.03)",
              border:"1px solid rgba(255,255,255,.08)",
              fontSize:12, color:"#a0a0b8", fontWeight:500,
              backdropFilter:"blur(8px)", cursor:"default",
              transition:"all .2s",
            }}
            onMouseEnter={e=>{e.currentTarget.style.background="rgba(108,99,255,.12)";e.currentTarget.style.borderColor="rgba(108,99,255,.4)";e.currentTarget.style.color="#e0e0ff";}}
            onMouseLeave={e=>{e.currentTarget.style.background="rgba(255,255,255,.03)";e.currentTarget.style.borderColor="rgba(255,255,255,.08)";e.currentTarget.style.color="#a0a0b8";}}>
              {t}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── STATS STRIP — CHANGE 2 ─────────────────── */
function StatsStrip() {
  const stats = [
    {value:"47",label:"early access devs",suffix:""},
    {value:"0.8ms",label:"avg recall latency",suffix:""},
    {value:"5:1",label:"engram compression",suffix:""},
    {value:"↑",label:"growing daily",suffix:""},
  ];
  return (
    <section className="relative py-8 border-y border-white/5 bg-gradient-to-r from-transparent via-[#0c0c1f] to-transparent">
      <div className="max-w-7xl mx-auto px-6 grid grid-cols-2 md:grid-cols-4 gap-6">
        {stats.map(s => (
          <div key={s.label} className="text-center">
            <div className="text-2xl md:text-3xl font-bold bg-gradient-to-r from-white to-[#A0A0B8] bg-clip-text text-transparent">{s.value}</div>
            <div className="text-xs text-gray-500 mt-1 uppercase tracking-wider">{s.label}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ─────────────────── HOW IT WORKS — NEW SECTION ─────────────────── */
function HowItWorks() {
  const steps = [
    { n:"01", title:"Install the SDK", code:"npm install nexmem-js", color:"#6C63FF", icon:Download },
    { n:"02", title:"Write Memories", code:`agent.remember({\n  type: 'episodic',\n  content: '...' })`, color:"#00E5D1", icon:Brain },
    { n:"03", title:"Recall Ranked Context", code:`agent.recall({\n  query: '...',\n  topK: 5 })`, color:"#FFB347", icon:Zap },
  ];
  return (
    <section className="relative py-24 px-6">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-14">
          <div className="text-xs uppercase tracking-widest text-[#6C63FF] mb-2">Quick start</div>
          <h2 className="text-3xl md:text-5xl font-bold">How it works</h2>
        </div>
        <div className="relative grid md:grid-cols-3 gap-0">
          {steps.map((s, i) => {
            const Icon = s.icon;
            return (
              <div key={i} className="flex flex-col md:flex-row items-stretch">
                <div className="flex-1 relative group p-7 rounded-2xl border border-white/8 bg-gradient-to-b from-white/[.025] to-transparent hover:border-white/15 transition-all"
                  style={{ borderColor: "rgba(255,255,255,.08)" }}>
                  <div className="absolute -top-3 left-6 flex items-center gap-2">
                    <div className="px-2 py-0.5 rounded-md text-xs font-mono font-semibold"
                      style={{ background: `${s.color}20`, color: s.color, border: `1px solid ${s.color}40` }}>
                      {s.n}
                    </div>
                  </div>
                  <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-4 mt-2"
                    style={{ background: `${s.color}18` }}>
                    <Icon size={20} style={{ color: s.color }} />
                  </div>
                  <h3 className="font-bold text-lg mb-3">{s.title}</h3>
                  <pre className="font-mono text-xs rounded-xl p-3 overflow-x-auto"
                    style={{ background: "rgba(0,0,0,.4)", color: s.color, border: `1px solid ${s.color}25`, lineHeight: 1.7 }}>
                    {s.code}
                  </pre>
                </div>
                {/* connector arrow */}
                {i < 2 && (
                  <div className="hidden md:flex items-center px-2">
                    <div style={{
                      width: 28, height: 2,
                      background: `linear-gradient(90deg, ${steps[i].color}, ${steps[i+1].color})`,
                      animation: "connectorPulse 2s ease-in-out infinite",
                      borderRadius: 999,
                    }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── PROBLEM / SOLUTION ─────────────────── */
function ProblemSolution() {
  const problems = [
    "Your agent forgot the user's name.",
    "Your agent asked for the same preference again.",
    "Your context window overflowed with noise.",
    "No memory survived session end.",
    "Retrieval returned irrelevant chunks.",
  ];
  const solutions = [
    "Agent remembers everything, always.",
    "User preferences persist forever.",
    "Context compressed 5:1 before injection.",
    "Memories survive across all sessions.",
    "Graph-ranked + semantically relevant recall.",
  ];
  return (
    <section className="relative py-32 px-6">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-20">
          <div className="text-xs uppercase tracking-widest text-[#00E5D1] mb-3">The shift</div>
          <h2 className="text-4xl md:text-5xl font-bold">From <span className="text-gray-500 line-through">forgetting</span> to <span className="text-shimmer">remembering</span></h2>
        </div>
        <div className="grid md:grid-cols-2 gap-6">
          <div className="relative p-8 rounded-2xl border border-[#FF3860]/20 bg-gradient-to-b from-[#FF3860]/5 to-transparent">
            <div className="flex items-center gap-2 mb-6">
              <div className="w-2 h-2 rounded-full bg-[#FF3860]"/>
              <span className="text-xs uppercase tracking-widest text-[#FF3860]">Without Nexmem</span>
            </div>
            <h3 className="text-2xl font-bold mb-6 text-gray-400">The Problem</h3>
            <div className="relative h-16 mb-6 opacity-40">
              <svg viewBox="0 0 300 60" className="w-full h-full">
                <line x1="0" y1="30" x2="80" y2="30" stroke="#FF3860" strokeWidth="1" strokeDasharray="4 4"/>
                <circle cx="80" cy="30" r="4" fill="#FF3860" opacity=".5"/>
                <line x1="80" y1="30" x2="140" y2="30" stroke="#444" strokeWidth="1" strokeDasharray="2 8"/>
                <circle cx="220" cy="30" r="4" fill="#FF3860" opacity=".3"/>
                <text x="150" y="16" fill="#FF3860" fontSize="9" textAnchor="middle" opacity=".6">CONNECTION LOST</text>
              </svg>
            </div>
            <ul className="space-y-3">
              {problems.map((p,i) => (
                <li key={i} className="flex items-start gap-3 text-gray-500">
                  <X size={16} className="mt-1 flex-shrink-0 text-[#FF3860]/60"/>
                  <span className="line-through decoration-gray-700">{p}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="relative p-8 rounded-2xl border border-[#6C63FF]/30 bg-gradient-to-b from-[#6C63FF]/10 to-transparent overflow-hidden">
            <div className="absolute -top-20 -right-20 w-60 h-60 rounded-full bg-[#6C63FF]/20 blur-3xl"/>
            <div className="absolute -bottom-20 -left-20 w-60 h-60 rounded-full bg-[#00E5D1]/10 blur-3xl"/>
            <div className="relative">
              <div className="flex items-center gap-2 mb-6">
                <div className="w-2 h-2 rounded-full bg-[#00E5D1] animate-pulse"/>
                <span className="text-xs uppercase tracking-widest text-[#00E5D1]">With Nexmem</span>
              </div>
              <h3 className="text-2xl font-bold mb-6">The Solution</h3>
              <div className="relative h-16 mb-6">
                <svg viewBox="0 0 300 60" className="w-full h-full">
                  <defs><linearGradient id="lg" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stopColor="#6C63FF"/><stop offset="50%" stopColor="#00E5D1"/><stop offset="100%" stopColor="#FFB347"/></linearGradient></defs>
                  <line x1="0" y1="30" x2="300" y2="30" stroke="url(#lg)" strokeWidth="1.5"/>
                  {[40,100,160,220,280].map((x,i)=>(
                    <g key={i}><circle cx={x} cy="30" r="6" fill="url(#lg)" opacity=".3"><animate attributeName="r" values="6;9;6" dur="2s" begin={`${i*.3}s`} repeatCount="indefinite"/></circle><circle cx={x} cy="30" r="3" fill="#fff"/></g>
                  ))}
                </svg>
              </div>
              <ul className="space-y-3">
                {solutions.map((s,i) => (
                  <li key={i} className="flex items-start gap-3 text-white">
                    <Check size={16} className="mt-1 flex-shrink-0 text-[#00E5D1]"/>
                    <span>{s}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── MEMORY TYPES ─────────────────── */
function MemoryTypes() {
  const [hovered, setHovered] = useState<number | null>(null);
  const types = [
    { name:"Episodic", desc:"Every interaction, timestamped forever.", color:"#A0C8FF", icon:Activity, code:"event_id, timestamp, content" },
    { name:"Semantic", desc:"Concepts indexed by meaning, retrieved by similarity.", color:"#00E5D1", icon:Brain, code:"embedding[384], hnsw_index" },
    { name:"Procedural", desc:"User preferences and workflows that shape every response.", color:"#7B68EE", icon:Layers, code:"pref_key, pref_value, weight" },
    { name:"Graph", desc:"Entity relationships that reveal hidden connections.", color:"#7CFC9B", icon:Network, code:"[User] --(prefers)--> [Python]" },
    { name:"Engrams", desc:"Compressed long-term facts. 5:1 ratio. Nothing forgotten.", color:"#FFB347", icon:Sparkles, code:"distilled_text, salience, hash" },
  ];
  return (
    <section className="relative py-32 px-6 overflow-hidden">
      <div className="absolute inset-0" style={{backgroundImage:"linear-gradient(rgba(108,99,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(108,99,255,.05) 1px,transparent 1px)",backgroundSize:"60px 60px",opacity:.4}}/>
      <div className="max-w-7xl mx-auto relative">
        <div className="text-center mb-20">
          <div className="text-xs uppercase tracking-widest text-[#7B68EE] mb-3">Architecture</div>
          <h2 className="text-4xl md:text-6xl font-bold mb-4">One brain. <span className="text-shimmer">Five types of memory.</span></h2>
          <p className="text-gray-400 max-w-2xl mx-auto">Nexmem splits memory into specialized layers — each tuned for the way agents actually think.</p>
        </div>
        <div className="relative max-w-4xl mx-auto" style={{perspective:"1500px"}}>
          <div className="space-y-3" style={{transformStyle:"preserve-3d",transform:"rotateX(15deg)"}}>
            {types.map((t,i) => {
              const Icon = t.icon, isH = hovered===i;
              return (
                <div key={t.name} onMouseEnter={()=>setHovered(i)} onMouseLeave={()=>setHovered(null)}
                  className="relative group cursor-pointer transition-all duration-500"
                  style={{transform:isH?"translateZ(40px) translateY(-8px)":"translateZ(0)",transformStyle:"preserve-3d"}}>
                  <div className="relative p-6 rounded-2xl border backdrop-blur-xl transition-all duration-500 flex items-center gap-5"
                    style={{background:`linear-gradient(135deg,${t.color}18 0%,rgba(10,10,26,.6) 100%)`,
                      borderColor:isH?t.color:"rgba(255,255,255,.08)",
                      boxShadow:isH?`0 0 50px ${t.color}30,0 20px 60px rgba(0,0,0,.5)`:"0 4px 20px rgba(0,0,0,.3)"}}>
                    <div className="flex items-center justify-center w-14 h-14 rounded-xl flex-shrink-0"
                      style={{background:`${t.color}20`,boxShadow:isH?`0 0 30px ${t.color}80`:"none"}}>
                      <Icon size={26} style={{color:t.color}}/>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 mb-1">
                        <h3 className="text-xl font-bold" style={{color:isH?t.color:"#fff"}}>{t.name}</h3>
                        <span className="text-xs text-gray-600 font-mono">0{i+1}</span>
                      </div>
                      <p className="text-sm text-gray-400 mb-2">{t.desc}</p>
                      <code className="text-xs font-mono text-gray-500 truncate block">{t.code}</code>
                    </div>
                    <ChevronRight size={18} className="text-gray-600 group-hover:text-white group-hover:translate-x-1 transition"/>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── ENGRAM SECTION — COPY CHANGES ─────────────────── */
function EngramSection() {
  return (
    <section className="relative py-32 px-6 overflow-hidden">
      <div className="absolute inset-0" style={{background:"radial-gradient(ellipse at center,rgba(255,179,71,.08) 0%,transparent 60%)"}}/>
      <div className="max-w-7xl mx-auto relative">
        <div className="text-center mb-16">
          <div className="text-xs uppercase tracking-widest text-[#FFB347] mb-3">The Differentiator</div>
          <h2 className="text-4xl md:text-6xl font-bold mb-4">We compress memory<br/><span className="text-shimmer">before storing it.</span></h2>
          {/* CHANGE 10b */}
          <p className="text-xl text-gray-400">5:1 compression. Verified. Nobody else does this.</p>
        </div>
        <div className="relative bg-gradient-to-b from-[#0c0c1f] to-[#080812] rounded-3xl border border-white/5 p-8 md:p-12 mb-12 overflow-hidden">
          <div className="absolute inset-0 opacity-30" style={{background:"radial-gradient(circle at 50% 50%,rgba(255,179,71,.15) 0%,transparent 50%)"}}/>
          <div className="relative grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-8 items-center">
            <div className="relative">
              <div className="text-xs uppercase tracking-widest text-gray-500 mb-3">Raw input · 58 tokens</div>
              <div className="relative h-48 overflow-hidden rounded-xl bg-black/40 border border-white/5 p-4 font-mono text-xs text-gray-500">
                <div className="space-y-1">
                  <div>"So like, the user — and I've been chatting</div>
                  <div>with them a lot — they really, really prefer</div>
                  <div>Python over JavaScript when it comes to</div>
                  <div>backend, you know? Also dark mode is a</div>
                  <div>must, and they don't like long answers,</div>
                  <div>they want concise responses, no fluff..."</div>
                </div>
                {[0,1,2,3].map(i=>(
                  <div key={i} className="absolute left-0 right-0 h-px bg-gradient-to-r from-transparent via-[#FFB347]/60 to-transparent"
                    style={{top:`${20+i*25}%`,animation:`flow ${3+i*.5}s ease-in-out infinite`,animationDelay:`${i*.4}s`}}/>
                ))}
              </div>
            </div>
            <div className="relative flex flex-col items-center justify-center py-8">
              <div className="relative w-32 h-32" style={{animation:"spin-slow 20s linear infinite"}}>
                <div className="absolute inset-0 rounded-full" style={{background:"conic-gradient(from 0deg,#FFB347,#7B68EE,#00E5D1,#FFB347)",filter:"blur(20px)",opacity:.6}}/>
                <div className="absolute inset-2 rounded-full bg-[#080812] border-2 border-[#FFB347]/40 flex items-center justify-center">
                  <div className="absolute inset-3 rounded-full" style={{background:"radial-gradient(circle,rgba(255,179,71,.4) 0%,transparent 70%)"}}/>
                  <Cpu size={36} className="text-[#FFB347] relative z-10" style={{filter:"drop-shadow(0 0 10px #FFB347)"}}/>
                </div>
                <div className="absolute inset-0 rounded-full border border-[#FFB347]/30" style={{animation:"spin-slow 8s linear infinite reverse"}}>
                  <div className="absolute -top-1 left-1/2 w-2 h-2 rounded-full bg-[#FFB347]" style={{boxShadow:"0 0 10px #FFB347"}}/>
                </div>
              </div>
              <div className="text-center mt-6">
                <div className="text-xs uppercase tracking-widest text-[#FFB347] mb-1">Engram Processor</div>
                <div className="text-2xl font-bold font-mono text-shimmer">5:1</div>
                <div className="text-xs text-gray-500">compression</div>
              </div>
            </div>
            <div className="relative">
              <div className="text-xs uppercase tracking-widest text-[#00E5D1] mb-3">Engram output · 12 tokens</div>
              <div className="relative h-48 rounded-xl bg-black/40 border border-[#00E5D1]/30 p-4 font-mono text-xs">
                <div className="space-y-2">
                  {[["user","#FFB347"],["prefers","#00E5D1"],["Python","#FFB347"],["over","#00E5D1"],["JS","#FFB347"],["·","#00E5D1"],["dark_mode","#7B68EE"],["·","#7B68EE"],["concise","#FFB347"],["answers","#00E5D1"]].map(([w,c],i)=>(
                    <span key={i} className="inline-block px-2 py-0.5 rounded mr-1 mb-1" style={{background:`${c}20`,color:c}}>{w}</span>
                  ))}
                </div>
                <div className="absolute bottom-3 right-3 text-[10px] text-gray-600 font-mono">salience: 0.94</div>
              </div>
            </div>
          </div>
          <div className="relative mt-8 pt-8 border-t border-white/5 grid grid-cols-3 gap-4 text-center text-xs">
            <div><div className="text-[#FFB347] mb-1 font-mono">spaCy NLP</div><div className="text-gray-500">entity + dependency parsing</div></div>
            <div><div className="text-[#00E5D1] mb-1 font-mono">Salience</div><div className="text-gray-500">important tokens rank higher</div></div>
            <div><div className="text-[#7B68EE] mb-1 font-mono">Co-occurrence</div><div className="text-gray-500">graph compression</div></div>
          </div>
        </div>
        <div className="grid md:grid-cols-3 gap-6">
          {[
            {title:"Lower LLM costs",desc:"Less context = cheaper API calls",icon:Coins},
            {title:"Less hallucination",desc:"Cleaner signal = more accurate recall",icon:Shield},
            {title:"Faster retrieval",desc:"Smaller vectors = faster HNSW search",icon:Zap},
          ].map((b,i)=>{const Icon=b.icon;return(
            <div key={i} className="group p-6 rounded-2xl border border-white/5 bg-white/[.02] hover:border-[#FFB347]/40 hover:bg-[#FFB347]/5 transition-all">
              <div className="w-10 h-10 rounded-lg bg-[#FFB347]/10 flex items-center justify-center mb-4"><Icon size={20} className="text-[#FFB347]"/></div>
              <h4 className="font-semibold mb-1">{b.title}</h4>
              <p className="text-sm text-gray-500">{b.desc}</p>
            </div>
          );})}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── TESTIMONIALS — NEW SECTION ─────────────────── */
function Testimonials() {
  const quotes = [
    { q:"Nexmem cut my LangChain agent's context cost by 40%. The engram compression is real.", handle:"@devbuilder_ai", role:"AI Agent Dev", initials:"DA", color:"#6C63FF" },
    { q:"Finally a memory layer that actually persists across sessions. Game changer for my Telegram bot.", handle:"@web3tinkerer", role:"Bot Developer", initials:"WT", color:"#00E5D1" },
    { q:"The on-chain anchoring is the feature I didn't know I needed. My users own their agent's memory.", handle:"@defiarchitect", role:"Web3 Builder", initials:"DA", color:"#FFB347" },
  ];
  return (
    <section className="relative py-24 px-6">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-14">
          <div className="text-xs uppercase tracking-widest text-[#7B68EE] mb-2">Early feedback</div>
          <h2 className="text-3xl md:text-5xl font-bold">Developers love it.</h2>
        </div>
        <div className="grid md:grid-cols-3 gap-6">
          {quotes.map((q,i) => (
            <div key={i} className="group relative p-7 rounded-2xl transition-all duration-300"
              style={{background:"rgba(255,255,255,.02)",border:"1px solid rgba(108,99,255,.2)",boxShadow:"0 0 0 rgba(108,99,255,0)"}}
              onMouseEnter={e=>{e.currentTarget.style.borderColor=`${q.color}50`;e.currentTarget.style.boxShadow=`0 0 40px ${q.color}25`;}}
              onMouseLeave={e=>{e.currentTarget.style.borderColor="rgba(108,99,255,.2)";e.currentTarget.style.boxShadow="0 0 0 rgba(108,99,255,0)";}}>
              <div className="text-5xl font-serif leading-none mb-4" style={{color:`${q.color}40`,lineHeight:1}}>"</div>
              <p className="text-gray-300 text-sm leading-relaxed mb-6">"{q.q}"</p>
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold"
                  style={{background:`${q.color}25`,color:q.color}}>{q.initials}</div>
                <div>
                  <div className="text-sm font-semibold" style={{color:q.color}}>{q.handle}</div>
                  <div className="text-xs text-gray-500">{q.role}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── API SECTION ─────────────────── */
function APISection() {
  const wc = `from nexmem import MemoryClient

client = MemoryClient(api_key="nxm_xxx")

await client.remember(
    "User prefers Python over JS, "
    "dark mode, concise answers.",
    app_id="my-agent"
)`;
  const rc = `context = await client.recall(
    query="how does this user "
          "like answers?",
    limit=5
)

print(context.memories.content)
# → "User prefers concise answers"`;
  return (
    <section className="relative py-32 px-6 overflow-hidden">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-16">
          <div className="text-xs uppercase tracking-widest text-[#6C63FF] mb-3">The API</div>
          <h2 className="text-4xl md:text-6xl font-bold mb-4">Two endpoints to <span className="text-shimmer">rule them all.</span></h2>
          <p className="text-gray-400 max-w-xl mx-auto">One unified interface. Writes to all memory types. Recalls a ranked context.</p>
        </div>
        <div className="grid md:grid-cols-2 gap-6" style={{perspective:"2000px"}}>
          <CodeTerminal title="POST /memory/episode/write" subtitle="Remember anything" code={wc} accent="#6C63FF" ry={6}/>
          <CodeTerminal title="POST /memory/context" subtitle="Recall ranked context" code={rc} accent="#00E5D1" ry={-6}/>
        </div>
        <div className="mt-12 flex flex-wrap justify-center gap-3">
          {["Python","TypeScript","MCP Server","REST","WebSocket"].map(s=>(
            <div key={s} className="px-4 py-2 rounded-full border border-white/10 bg-white/[.02] text-sm text-gray-300 hover:border-[#6C63FF]/40 hover:text-white transition">{s}</div>
          ))}
        </div>
      </div>
    </section>
  );
}

function CodeTerminal({ title, subtitle, code, accent, ry }: any) {
  const lines = code.split("\n");
  return (
    <div className="relative" style={{transformStyle:"preserve-3d",transform:`rotateY(${ry}deg) rotateX(2deg)`}}>
      <div className="absolute -inset-2 rounded-2xl blur-2xl opacity-25" style={{background:accent}}/>
      <div className="relative rounded-2xl border border-white/10 bg-[#0a0a18] overflow-hidden shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 bg-black/40 border-b border-white/5">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-[#FF5F57]"/><div className="w-3 h-3 rounded-full bg-[#FFBD2E]"/><div className="w-3 h-3 rounded-full bg-[#28CA42]"/>
          </div>
          <div className="text-xs text-gray-500 font-mono">{title}</div>
          <div className="w-12"/>
        </div>
        <div className="p-5">
          <div className="text-xs text-gray-500 uppercase tracking-widest mb-3 flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full" style={{background:accent,boxShadow:`0 0 8px ${accent}`}}/>
            {subtitle}
          </div>
          <pre className="font-mono text-sm leading-relaxed overflow-x-auto">
            {lines.map((line: string, i: number)=>(
              <div key={i} className="flex">
                <span className="text-gray-600 mr-4 select-none w-5 text-right">{i+1}</span>
                <code className="flex-1" dangerouslySetInnerHTML={{__html:hpy(line)}}/>
              </div>
            ))}
          </pre>
        </div>
      </div>
    </div>
  );
}

function hpy(l: any) {
  return l
    .replace(/(#.*$)/g,'<span style="color:#5a5a6e;font-style:italic">$1</span>')
    .replace(/\b(from|import|await|print|return|def|class)\b/g,'<span style="color:#FF79C6">$1</span>')
    .replace(/\b(client|context|MemoryClient)\b/g,'<span style="color:#8BE9FD">$1</span>')
    .replace(/\.(remember|recall|memories|content)\b/g,'.<span style="color:#50FA7B">$1</span>')
    .replace(/(".*?")/g,'<span style="color:#F1FA8C">$1</span>')
    .replace(/\b(\d+)\b/g,'<span style="color:#FFB347">$1</span>')
    .replace(/(api_key|app_id|query|limit)=/g,'<span style="color:#FFB347">$1</span>=');
}

/* ─────────────────── WEB3 SECTION — CHANGE 10a ─────────────────── */
function Web3Section() {
  const features = [
    {icon:Wallet,title:"Wallet Identity",desc:"Sign in with your Ethereum or Solana wallet. Memories are tied to your on-chain identity.",color:"#00E5D1"},
    {icon:Link2,title:"On-Chain Anchoring",desc:"Every memory batch is hashed and anchored on-chain. Verifiable, tamper-proof, forever.",color:"#7B68EE"},
    {icon:Cloud,title:"Decentralized Export",desc:"Export all your memories as encrypted blobs to Arweave. Only your wallet key unlocks them.",color:"#FFB347"},
  ];
  return (
    <section className="relative py-32 px-6 overflow-hidden">
      <div className="absolute inset-0 opacity-20">
        <svg width="100%" height="100%"><defs><pattern id="circuit" x="0" y="0" width="120" height="120" patternUnits="userSpaceOnUse">
          <path d="M 0 60 L 40 60 L 50 50 L 70 50 L 80 60 L 120 60" fill="none" stroke="#00E5D1" strokeWidth=".5" opacity=".4"/>
          <path d="M 60 0 L 60 40 L 50 50 M 60 80 L 60 120" fill="none" stroke="#00E5D1" strokeWidth=".5" opacity=".4"/>
          <circle cx="50" cy="50" r="2" fill="#00E5D1" opacity=".6"/><circle cx="70" cy="50" r="2" fill="#00E5D1" opacity=".6"/>
        </pattern></defs><rect width="100%" height="100%" fill="url(#circuit)"/></svg>
      </div>
      <div className="max-w-7xl mx-auto relative">
        <div className="text-center mb-16">
          <div className="text-xs uppercase tracking-widest text-[#00E5D1] mb-3">Web3 Native</div>
          {/* CHANGE 10a */}
          <h2 className="text-4xl md:text-6xl font-bold mb-4">Own your agent's memory.<br/><span className="text-shimmer">On-chain, forever.</span></h2>
          <p className="text-gray-400">Your memories, your wallet, your chain.</p>
        </div>
        <div className="grid md:grid-cols-3 gap-6">
          {features.map((f,i)=>{const Icon=f.icon;return(
            <div key={i} className="group relative p-8 rounded-2xl border border-white/10 bg-gradient-to-b from-white/[.03] to-transparent hover:border-white/20 transition-all overflow-hidden">
              <div className="absolute -top-20 -right-20 w-40 h-40 rounded-full opacity-0 group-hover:opacity-30 blur-3xl transition-opacity duration-500" style={{background:f.color}}/>
              <div className="relative">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-6" style={{background:`${f.color}15`,boxShadow:`0 0 30px ${f.color}40`}}>
                  <Icon size={26} style={{color:f.color}}/>
                </div>
                <h3 className="text-xl font-bold mb-3">{f.title}</h3>
                <p className="text-gray-400 text-sm leading-relaxed">{f.desc}</p>
              </div>
            </div>
          );})}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── COMPARISON — CHANGE 6 (tooltips) ─────────────────── */
function Comparison() {
  const partialTips = {
    mem0:"Supports episodic + semantic only. No procedural or on-chain memory.",
    supermemory:"Supports episodic, semantic, procedural, and graph. Missing on-chain features.",
    zep:"Supports episodic + semantic. No procedural, graph, or Web3 memory.",
  };
  const rows = [
    {name:"Engram Compression (5:1)",nexmem:"yes",mem0:"no",sm:"no",zep:"no"},
    {name:"All 5 Memory Types",nexmem:"yes",mem0:"partial",sm:"partial",zep:"partial"},
    {name:"Web3 / Wallet Identity",nexmem:"yes",mem0:"no",sm:"no",zep:"no"},
    {name:"On-Chain Anchoring",nexmem:"yes",mem0:"no",sm:"no",zep:"no"},
    {name:"User-Owned Decentralized",nexmem:"yes",mem0:"no",sm:"no",zep:"no"},
    {name:"Self-Hostable",nexmem:"yes",mem0:"yes",sm:"yes",zep:"yes"},
    {name:"MCP Server",nexmem:"yes",mem0:"no",sm:"no",zep:"no"},
  ];

  const cell = (val: string, tipKey?: keyof typeof partialTips) => {
    if (val === "yes") return <Check size={18} className="text-[#00E5D1] mx-auto"/>;
    if (val === "no") return <X size={18} className="text-gray-700 mx-auto"/>;
    return (
      <div className="tip-wrap mx-auto w-fit">
        <span style={{fontSize:11,color:"#FFB347",borderBottom:"1px dashed rgba(255,179,71,.5)",paddingBottom:1}}>Partial</span>
        <div className="tip-box">{tipKey ? partialTips[tipKey] : ""}</div>
      </div>
    );
  };

  return (
    <section className="relative py-32 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <div className="text-xs uppercase tracking-widest text-gray-500 mb-3">Comparison</div>
          <h2 className="text-4xl md:text-6xl font-bold">Built for what <span className="text-shimmer">others forgot.</span></h2>
        </div>
        <div className="relative rounded-2xl border border-white/10 bg-gradient-to-b from-white/[.02] to-transparent overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="text-left px-6 py-5 text-gray-400 font-medium">Feature</th>
                  <th className="px-6 py-5 relative">
                    <div className="absolute inset-x-2 inset-y-0 rounded-t-xl bg-gradient-to-b from-[#6C63FF]/20 to-[#7B68EE]/5 border-x border-t border-[#6C63FF]/40"/>
                    <div className="relative font-bold text-white flex items-center justify-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-[#00E5D1] animate-pulse"/>Nexmem
                    </div>
                  </th>
                  <th className="px-6 py-5 text-gray-500 font-medium">Mem0</th>
                  <th className="px-6 py-5 text-gray-500 font-medium">Supermemory</th>
                  <th className="px-6 py-5 text-gray-500 font-medium">Zep</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row,i)=>(
                  <tr key={i} className="border-b border-white/5 hover:bg-white/[.02] transition">
                    <td className="px-6 py-4 text-gray-300">{row.name}</td>
                    <td className="px-6 py-4 text-center relative">
                      <div className="absolute inset-x-2 inset-y-0 bg-[#6C63FF]/5 border-x border-[#6C63FF]/40"/>
                      <div className="relative">{cell(row.nexmem)}</div>
                    </td>
                    <td className="px-6 py-4 text-center">{cell(row.mem0,"mem0")}</td>
                    <td className="px-6 py-4 text-center">{cell(row.sm,"supermemory")}</td>
                    <td className="px-6 py-4 text-center">{cell(row.zep,"zep")}</td>
                  </tr>
                ))}
                <tr><td className="px-6 py-2"/><td className="px-6 py-2 relative"><div className="absolute inset-x-2 inset-y-0 rounded-b-xl bg-gradient-to-t from-[#6C63FF]/20 to-[#6C63FF]/5 border-x border-b border-[#6C63FF]/40"/></td><td colSpan={3}/></tr>
              </tbody>
            </table>
          </div>
        </div>
        <p className="text-center text-xs text-gray-600 mt-4">Data verified April 2026.</p>
      </div>
    </section>
  );
}

/* ─────────────────── USE CASES — CHANGE 8 ─────────────────── */
function UseCases() {
  const cases = [
    {icon:MessageCircle,title:"Telegram / WhatsApp Bots",desc:"Bots that remember every user's name, preferences, and history.",color:"#00E5D1",href:"/docs/guides/telegram-bots",soon:false},
    {icon:Coins,title:"DeFi / Crypto Copilots",desc:"Trading assistants that remember your risk tolerance and portfolio goals.",color:"#FFB347",href:null,soon:true},
    {icon:Terminal,title:"Coding Assistants",desc:"VS Code and Cursor agents that remember your project conventions.",color:"#7B68EE",href:null,soon:true},
    {icon:Headphones,title:"Customer Support Agents",desc:"Support bots that never ask for the same information twice.",color:"#A0C8FF",href:"/docs/guides/support-agents",soon:false},
  ];
  return (
    <section className="relative py-32 px-6">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-16">
          <div className="text-xs uppercase tracking-widest text-[#7B68EE] mb-3">Use Cases</div>
          <h2 className="text-4xl md:text-6xl font-bold">What developers are<br/><span className="text-shimmer">building with Nexmem.</span></h2>
        </div>
        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-5">
          {cases.map((c,i)=>{const Icon=c.icon;return(
            <div key={i} className="group relative p-6 rounded-2xl border border-white/10 bg-gradient-to-b from-white/[.03] to-transparent hover:border-white/20 hover:-translate-y-1 transition-all overflow-hidden cursor-pointer">
              {c.soon && (
                <div className="absolute top-3 right-3 px-2 py-0.5 rounded-full text-xs bg-white/5 border border-white/10 text-gray-500">Coming soon</div>
              )}
              <div className="absolute -top-20 -right-20 w-40 h-40 rounded-full opacity-0 group-hover:opacity-20 blur-3xl transition-opacity" style={{background:c.color}}/>
              <div className="relative">
                <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5" style={{background:`${c.color}15`}}>
                  <Icon size={22} style={{color:c.color}}/>
                </div>
                <h3 className="font-bold mb-2 leading-tight">{c.title}</h3>
                <p className="text-sm text-gray-400 mb-4 leading-relaxed">{c.desc}</p>
                {c.soon ? (
                  <span className="text-sm text-gray-600 italic">Guide coming soon</span>
                ) : (
                  <a href={c.href || "#"} className="text-sm font-medium flex items-center gap-1 group-hover:gap-2 transition-all" style={{color:c.color}}>
                    Build this <ArrowRight size={14}/>
                  </a>
                )}
              </div>
            </div>
          );})}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── PRICING — CHANGE 7 ─────────────────── */
function Pricing() {
  const [annual, setAnnual] = useState(false);
  const monthly = [null, 9, 29, null];
  const tiers = [
    {name:"Free",price:"$0",period:"/month",features:["1,000 writes/month","5,000 reads/month","1 API key","Community support"],cta:"Get Started Free",popular:false,idx:0},
    {name:"Starter",price:"$9",period:"/month",features:["50,000 writes","200,000 reads","5 API keys","Webhook support","Email support"],cta:"Start Free Trial",popular:true,idx:1},
    {name:"Pro",price:"$29",period:"/month",features:["500,000 writes","2M reads","Unlimited API keys","Connectors included","Priority support"],cta:"Start Free Trial",popular:false,idx:2},
    {name:"Enterprise",price:"Custom",period:"",features:["Unlimited everything","On-chain anchoring","Wallet identity","SLA + dedicated Slack"],cta:"Talk to Us",popular:false,idx:3},
  ];
  const getPrice = (t: any) => {
    if (t.name === "Free") return "$0";
    if (t.name === "Enterprise") return "Custom";
    const base = monthly[t.idx];
    if (base === null) return "$0";
    return annual ? `$${(base * 0.8).toFixed(1)}` : `$${base}`;
  };
  const getPeriod = (t: any) => {
    if (t.name === "Enterprise") return "";
    return annual ? "/mo, billed annually" : "/month";
  };
  return (
    <section id="pricing" className="relative py-32 px-6">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-10">
          <div className="text-xs uppercase tracking-widest text-[#00E5D1] mb-3">Pricing</div>
          <h2 className="text-4xl md:text-6xl font-bold mb-8">Start free. <span className="text-shimmer">Scale when your agents do.</span></h2>
          {/* Toggle */}
          <div className="inline-flex items-center gap-1 p-1 rounded-full bg-white/5 border border-white/10">
            {[{label:"Monthly",val:false},{label:"Annual",val:true}].map(o=>(
              <button key={o.label} onClick={()=>setAnnual(o.val)}
                className="relative px-5 py-2 rounded-full text-sm font-medium transition-all"
                style={{background:annual===o.val?"#6C63FF":"transparent",color:annual===o.val?"#fff":"#888"}}>
                {o.label}
                {o.val && !annual && <span className="ml-2 text-xs px-1.5 py-0.5 rounded-full" style={{background:"rgba(0,229,209,.15)",color:"#00E5D1"}}>-20%</span>}
              </button>
            ))}
          </div>
          {annual && (
            <div className="mt-3 text-sm text-[#00E5D1] animate-fade-in">You're saving 20% with annual billing 🎉</div>
          )}
        </div>
        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-5 mt-6">
          {tiers.map((t,i)=>(
            <div key={i} className={`relative rounded-2xl p-6 border transition-all ${t.popular?"border-[#6C63FF]/50 bg-gradient-to-b from-[#6C63FF]/10 to-transparent scale-105 lg:-translate-y-2":"border-white/10 bg-white/[.02] hover:border-white/20"}`}>
              {t.popular && (
                <>
                  <div className="absolute -inset-px rounded-2xl bg-gradient-to-r from-[#6C63FF] via-[#7B68EE] to-[#00E5D1] opacity-40 blur-md -z-10"/>
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full bg-gradient-to-r from-[#6C63FF] to-[#7B68EE] text-xs font-medium flex items-center gap-1">
                    <Star size={12} fill="white"/>Most popular
                  </div>
                </>
              )}
              <div className="text-sm text-gray-400 mb-2">{t.name}</div>
              <div className="flex items-baseline gap-1 mb-1 flex-wrap">
                <span className="text-4xl font-bold">{getPrice(t)}</span>
              </div>
              <div className="text-xs text-gray-500 mb-5 h-4">{getPeriod(t)}</div>
              <ul className="space-y-2.5 mb-8 min-h-[150px]">
                {t.features.map((f,j)=>(
                  <li key={j} className="flex items-start gap-2 text-sm text-gray-300">
                    <Check size={14} className={`mt-1 flex-shrink-0 ${t.popular?"text-[#00E5D1]":"text-gray-500"}`}/>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
              <button className={`w-full py-3 rounded-xl font-medium text-sm transition-all ${t.popular?"bg-gradient-to-r from-[#6C63FF] to-[#7B68EE] hover:opacity-90":"border border-white/20 hover:bg-white/5 hover:border-white/40"}`}>
                {t.cta}
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── FAQ — NEW SECTION ─────────────────── */
const FAQ_DATA = [
  {
    q:"Is wallet authentication required to use Nexmem?",
    a:"No. Wallet auth is completely optional. You can use Nexmem with a standard API key and email/password auth. Wallet-based identity (Sign-In with Ethereum or Solana) is an additional layer for developers who want user-owned, verifiable memories anchored on-chain."
  },
  {
    q:"Can I self-host Nexmem for my project?",
    a:"Yes. Nexmem is fully self-hostable via Docker. The core stack is FastAPI + PostgreSQL + pgvector — all open infrastructure. The self-hosted version includes all 5 memory types, the Engram Processor, and the graph memory layer. On-chain anchoring requires an Arweave/Irys API key but is otherwise supported."
  },
  {
    q:"How does on-chain memory anchoring work in practice?",
    a:"When you call POST /memory/episode/write, Nexmem batches new engrams and hashes them using SHA-256. The hash is submitted to Arweave via the Irys bundler. You receive a transaction ID. Later, GET /memory/provenance/{engram_id} returns the on-chain tx hash, which you can verify independently — proving your memory data was not altered."
  },
  {
    q:"What's the difference between Engram compression and standard summarization?",
    a:"Summarization uses an LLM to paraphrase. Engrams are structurally compressed using spaCy NLP: entity extraction, dependency parsing, negation detection, and salience scoring. The result is a structured fact graph — not a paraphrase. No LLM is needed for compression. This means it's 10x cheaper, deterministic, and doesn't hallucinate structural facts."
  },
  {
    q:"Which LLM frameworks does Nexmem natively support?",
    a:"Nexmem has native SDKs for Python and TypeScript, plus an MCP Server for direct LLM tool-calling. It integrates with LangChain (memory adapter), AutoGen (custom memory class), CrewAI (agent memory hook), LlamaIndex (retriever plugin), and any framework that supports REST API calls. Telegram and Discord bot integrations are documented in the guides."
  },
  {
    q:"How is my data stored and who owns it?",
    a:"Your data is stored in your PostgreSQL instance (self-hosted) or Nexmem's managed cluster (cloud). In cloud mode, your data is logically isolated per app_id and API key. With wallet identity enabled, your data can be exported as an encrypted blob to Arweave — at which point only your wallet's private key can decrypt it. Nexmem never has access to your private key."
  },
];

function FAQ() {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <section className="relative py-24 px-6">
      <div className="max-w-3xl mx-auto">
        <div className="text-center mb-14">
          <div className="text-xs uppercase tracking-widest text-[#6C63FF] mb-2">Questions</div>
          <h2 className="text-3xl md:text-5xl font-bold">Frequently asked</h2>
        </div>
        <div className="space-y-3">
          {FAQ_DATA.map((item,i)=>{
            const isOpen = open===i;
            return (
              <div key={i} className="rounded-2xl border transition-all duration-300"
                style={{background:"rgba(255,255,255,.02)",borderColor:isOpen?"rgba(108,99,255,.4)":"rgba(255,255,255,.07)"}}>
                <button className="w-full text-left flex items-start justify-between gap-4 px-6 py-5"
                  onClick={()=>setOpen(isOpen?null:i)}>
                  <span className="font-medium text-sm md:text-base leading-snug">{item.q}</span>
                  <ChevronDown size={18} className="flex-shrink-0 mt-0.5 text-gray-400 transition-transform duration-300"
                    style={{transform:isOpen?"rotate(180deg)":"rotate(0deg)"}}/>
                </button>
                <div style={{maxHeight:isOpen?"400px":"0",opacity:isOpen?1:0,overflow:"hidden",transition:"max-height .4s cubic-bezier(.16,1,.3,1), opacity .3s ease"}}>
                  <p className="px-6 pb-5 text-sm text-gray-400 leading-relaxed">{item.a}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────── FINAL CTA — CHANGE 10c ─────────────────── */
function FinalCTA() {
  const particles = useMemo(()=>Array.from({length:30}).map((_,i)=>({
    left:`${Math.round(Math.random()*100)}%`,
    top:`${Math.round(Math.random()*100)}%`,
    dur:`${3+Math.random()*4}s`,
    delay:`${Math.random()*3}s`,
    color:["#6C63FF","#00E5D1","#FFB347"][i%3],
  })),[]);
  return (
    <section className="relative py-32 px-6 overflow-hidden">
      <div className="absolute inset-0" style={{backgroundImage:"linear-gradient(rgba(108,99,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(108,99,255,.05) 1px,transparent 1px)",backgroundSize:"60px 60px",opacity:.5}}/>
      <div className="absolute inset-0" style={{background:"radial-gradient(ellipse at center,rgba(108,99,255,.2) 0%,transparent 50%)"}}/>
      <div className="absolute inset-0 pointer-events-none">
        {particles.map((p,i)=>(
          <div key={i} className="absolute w-1 h-1 rounded-full bg-white"
            style={{left:p.left,top:p.top,animation:`synaptic ${p.dur} ease-in-out ${p.delay} infinite`,boxShadow:`0 0 6px ${p.color}`}}/>
        ))}
      </div>
      <div className="relative max-w-4xl mx-auto text-center">
        <h2 className="text-5xl md:text-7xl font-bold mb-6 leading-tight">Your agent's memory<br/><span className="text-shimmer">starts here.</span></h2>
        <p className="text-xl text-gray-400 mb-10">
          Free forever. No credit card.{" "}
          {/* CHANGE 10c */}
          <span className="text-white">First memory stored in under 60 seconds.</span>
        </p>
        <GlowButton className="text-lg px-10 py-5 mx-auto">
          Get Your Free API Key <ArrowRight size={20} className="group-hover:translate-x-1 transition"/>
        </GlowButton>
        <div className="mt-6 text-sm text-gray-500">1,000 writes/month free · Cancel anytime · No card required</div>
      </div>
    </section>
  );
}

/* ─────────────────── FOOTER — CHANGE 10d ─────────────────── */
function Footer() {
  return (
    <footer className="relative border-t border-white/5 py-16 px-6">
      <div className="max-w-7xl mx-auto">
        <div className="grid md:grid-cols-5 gap-10 mb-12">
          <div className="md:col-span-2">
            <div className="flex items-center gap-2 mb-4">
              <NexmemNode />
              <span className="text-xl font-bold">Nexmem</span>
            </div>
            {/* CHANGE 10d */}
            <p className="text-sm text-gray-500 max-w-xs">Next-gen memory for AI agents..</p>
          </div>
          {[
            {title:"Product",links:["Features","Pricing","Changelog"]},
            {title:"Developers",links:["Docs","SDKs","MCP Server","GitHub"]},
            {title:"Company",links:["Blog","About","Status"]},
          ].map(g=>(
            <div key={g.title}>
              <h4 className="text-sm font-semibold text-white mb-4">{g.title}</h4>
              <ul className="space-y-2.5">
                {g.links.map(l=>(
                  <li key={l}><a href="#" className="text-sm text-gray-500 hover:text-white transition">{l}</a></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="flex flex-col md:flex-row items-center justify-between pt-8 border-t border-white/5 gap-4">
          <div className="flex items-center gap-4 text-xs text-gray-600">
            <a href="#" className="hover:text-white transition">Privacy</a>
            <a href="#" className="hover:text-white transition">Terms</a>
            <span>© 2026 Nexmem · Built for the agentic web</span>
          </div>
          <div className="flex items-center gap-3">
            {[MessageCircle,Globe].map((Icon,i)=>(
              <a key={i} href="#" className="w-9 h-9 rounded-lg border border-white/10 flex items-center justify-center text-gray-500 hover:text-white hover:border-white/30 transition">
                <Icon size={16}/>
              </a>
            ))}
          </div>
        </div>
      </div>
    </footer>
  );
}
