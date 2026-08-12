"use client";

import {
  Activity,
  ArrowRight,
  Camera,
  CheckCircle2,
  ChevronRight,
  Eye,
  MapPin,
  Radio,
  ShieldAlert,
  Wrench,
} from "lucide-react";
import { useState } from "react";

type Direction = "signal" | "fieldbook" | "panorama";

const DIRECTIONS: Array<{
  id: Direction;
  number: string;
  name: string;
  subtitle: string;
  purpose: string;
  accent: string;
}> = [
  {
    id: "signal",
    number: "01",
    name: "Signal",
    subtitle: "사건 우선 관제",
    purpose: "긴급 상황을 가장 먼저 보고 처리하는 관제실",
    accent: "bg-red-500",
  },
  {
    id: "fieldbook",
    number: "02",
    name: "Fieldbook",
    subtitle: "설치와 점검의 흐름",
    purpose: "시공자와 현장 관리자가 카메라를 인수하는 과정",
    accent: "bg-amber-500",
  },
  {
    id: "panorama",
    number: "03",
    name: "Panorama",
    subtitle: "공간 인지 라이브 월",
    purpose: "여러 카메라의 상태와 위치를 동시에 파악하는 관제실",
    accent: "bg-cyan-400",
  },
];

export function DesignPrototype() {
  const [selected, setSelected] = useState<Direction>("signal");
  const active = DIRECTIONS.find((direction) => direction.id === selected)!;

  return (
    <div className="mx-auto max-w-[1540px] space-y-8 pb-8">
      <header className="grid gap-6 border-b border-border pb-7 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] text-muted-foreground">KSI CCTV / DESIGN PROTOTYPE</p>
          <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">
            관제의 우선순위를 먼저 고릅니다.
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
            이 화면은 실제 카메라나 사건 데이터를 변경하지 않는 목업입니다. 세 방향은 같은 현장 정보를 서로 다른 순서와 밀도로 보여줍니다.
          </p>
        </div>
        <div className="rounded-2xl border bg-muted/35 p-4 text-sm leading-6">
          <p className="font-medium">선택 기준</p>
          <p className="mt-1 text-muted-foreground">가장 많은 시간을 쓰는 사용자가 관제자면 Signal 또는 Panorama, 설치·인수 담당자면 Fieldbook이 적합합니다.</p>
        </div>
      </header>

      <section className="grid gap-3 md:grid-cols-3" aria-label="디자인 방향 선택">
        {DIRECTIONS.map((direction) => {
          const selectedDirection = direction.id === selected;
          return (
            <button
              key={direction.id}
              type="button"
              aria-pressed={selectedDirection}
              onClick={() => setSelected(direction.id)}
              className={`group relative min-h-40 overflow-hidden rounded-2xl border p-5 text-left transition ${selectedDirection ? "border-foreground bg-foreground text-background shadow-lg" : "bg-card hover:border-foreground/40 hover:bg-muted/50"}`}
            >
              <span className={`absolute left-0 top-0 h-full w-1 ${direction.accent}`} aria-hidden="true" />
              <div className="flex items-start justify-between gap-3">
                <span className={`font-mono text-xs ${selectedDirection ? "text-background/65" : "text-muted-foreground"}`}>{direction.number}</span>
                {selectedDirection && <CheckCircle2 className="size-5" aria-label="선택됨" />}
              </div>
              <p className="mt-5 text-xl font-semibold">{direction.name}</p>
              <p className={`mt-1 text-sm ${selectedDirection ? "text-background/75" : "text-muted-foreground"}`}>{direction.subtitle}</p>
              <p className={`mt-3 text-xs ${selectedDirection ? "text-background/65" : "text-muted-foreground"}`}>{direction.purpose}</p>
            </button>
          );
        })}
      </section>

      <section aria-live="polite" aria-label={`${active.name} 목업`}>
        {selected === "signal" && <SignalMockup />}
        {selected === "fieldbook" && <FieldbookMockup />}
        {selected === "panorama" && <PanoramaMockup />}
      </section>

      <section className="grid gap-4 border-t pt-7 md:grid-cols-3">
        <DirectionNote label="Signal을 고르면" text="Dashboard와 모바일 첫 화면이 사건 대기열이 됩니다. 라이브 영상은 사건 판단을 위한 증거 역할을 합니다." />
        <DirectionNote label="Fieldbook을 고르면" text="카메라 등록, 영역 설정, 연결 시험, 인수인계를 하나의 site checklist로 재구성합니다." />
        <DirectionNote label="Panorama를 고르면" text="라이브 월이 제품의 중심이 됩니다. 카메라 상태와 현장 위치를 빠르게 훑는 관제 환경에 맞춥니다." />
      </section>
    </div>
  );
}

function DirectionNote({ label, text }: { label: string; text: string }) {
  return <div className="border-l-2 border-border pl-4"><p className="font-medium">{label}</p><p className="mt-1 text-sm leading-6 text-muted-foreground">{text}</p></div>;
}

function SignalMockup() {
  return (
    <MockupFrame label="01 / SIGNAL" title="지금 처리해야 할 사건" note="위험도를 가장 빠르게 판단하고, 한 화면에서 라이브·증거·조치를 연결합니다." tone="dark">
      <div className="grid min-h-[580px] gap-px bg-white/10 xl:grid-cols-[250px_minmax(0,1fr)_310px]">
        <aside className="bg-[#16191c] p-4 text-zinc-300">
          <SignalBrand />
          <p className="mt-8 text-[11px] font-medium tracking-[0.12em] text-zinc-500">OPEN INCIDENTS</p>
          <div className="mt-3 space-y-2">
            <IncidentRow active title="지게차 접근 감지" camera="창고 A-03" level="긴급" />
            <IncidentRow title="카메라 영상 단절" camera="출입구 A-01" level="상태" />
            <IncidentRow title="보호구 미착용" camera="작업장 B-07" level="고위험" />
          </div>
          <div className="mt-8 border-t border-white/10 pt-4 text-xs text-zinc-500"><p>미종결 3건</p><p className="mt-1">오늘 종결 12건</p></div>
        </aside>
        <main className="bg-[#101214] p-4 text-zinc-100 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><p className="text-xs text-red-300">CRITICAL / 00:42 OPEN</p><h2 className="mt-1 text-xl font-semibold">지게차 접근 감지</h2><p className="mt-1 text-sm text-zinc-400">창고 A-03 · 적재 구역 남측</p></div>
            <span className="rounded-full bg-red-500 px-3 py-1 text-xs font-semibold text-white">신규 사건</span>
          </div>
          <div className="relative mt-6 aspect-[16/8] overflow-hidden rounded-xl border border-white/10 bg-[radial-gradient(circle_at_65%_35%,#475569_0,#1c2530_33%,#0d1117_75%)]">
            <div className="absolute bottom-[17%] left-[13%] h-[42%] w-[20%] rounded-sm border-2 border-red-400/80" />
            <div className="absolute bottom-[17%] left-[35%] h-[30%] w-[18%] rounded-sm border-2 border-amber-300/80" />
            <div className="absolute left-4 top-4 flex items-center gap-2 rounded bg-black/60 px-2 py-1 font-mono text-[10px] text-zinc-200"><Radio className="size-3 text-emerald-400" />LIVE / A-03 / 영상 수신 정상</div>
            <div className="absolute bottom-4 left-4 rounded bg-red-500/90 px-2 py-1 text-xs font-medium">위험 구역 진입</div>
            <div className="absolute bottom-[17%] left-[31%] h-px w-[30%] bg-amber-300/70" />
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-3"><Stat label="영상 수신" value="정상" tone="ready" /><Stat label="제한 구역" value="미설정" tone="attention" /><Stat label="감지 설정" value="2개 보류" tone="attention" /></div>
        </main>
        <aside className="bg-[#16191c] p-4 text-zinc-100">
          <p className="text-[11px] font-medium tracking-[0.12em] text-zinc-500">OPERATOR ACTION</p>
          <h3 className="mt-3 font-semibold">첫 조치를 기록하세요</h3>
          <div className="mt-4 space-y-3"><ActionButton primary label="사건 확인" /><ActionButton label="대응 시작" /><ActionButton label="현장에 알림 전송" /></div>
          <div className="mt-6 rounded-lg border border-white/10 bg-white/5 p-3 text-sm"><p className="font-medium">AI 증거</p><p className="mt-1 leading-5 text-zinc-400">보행자와 지게차의 간격이 제한 구역 기준보다 짧습니다.</p></div>
          <div className="mt-5 border-t border-white/10 pt-4 text-xs text-zinc-400"><p>10:31:04 AI 감지</p><p className="mt-2">10:31:06 증거 저장</p><p className="mt-2">대기 중: 운영자 확인</p></div>
        </aside>
      </div>
      <MobileSignal />
    </MockupFrame>
  );
}

function FieldbookMockup() {
  return (
    <MockupFrame label="02 / FIELDBOOK" title="현장을 인수할 준비" note="설치자가 다음 작업을 즉시 알 수 있도록 카메라 설정과 commissioning을 하나의 이야기로 엮습니다." tone="paper">
      <div className="grid min-h-[580px] gap-5 bg-[#f4f0e8] p-5 text-[#27241f] lg:grid-cols-[220px_minmax(0,1fr)_270px]">
        <aside className="rounded-xl border border-[#d7cfbf] bg-[#fbf8f1] p-4">
          <div className="flex items-center gap-2"><div className="grid size-8 place-items-center rounded-lg bg-[#343028] text-white"><Wrench className="size-4" /></div><span className="font-semibold">현장 수첩</span></div>
          <ol className="mt-7 space-y-1 text-sm"><SetupStep complete label="현장 정보" /><SetupStep complete label="카메라 12대 등록" /><SetupStep current label="영역과 감지 설정" /><SetupStep label="인수 전 점검" /><SetupStep label="고객 인수" /></ol>
          <div className="mt-8 rounded-lg bg-[#ede5d7] p-3 text-xs leading-5 text-[#665d4e]"><p className="font-medium text-[#3b352b]">오늘의 목표</p><p className="mt-1">A동 4대의 제한 구역을 설정하고 연결 복구를 확인합니다.</p></div>
        </aside>
        <main className="rounded-xl border border-[#d7cfbf] bg-[#fbf8f1] p-5">
          <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-medium tracking-[0.14em] text-[#8b7d68]">COMMISSIONING / 72%</p><h2 className="mt-1 text-2xl font-semibold">영역과 감지 설정</h2><p className="mt-2 text-sm text-[#776c5a]">카메라가 보는 화면 위에서 실제 운영 범위를 표시합니다.</p></div><span className="rounded-full border border-[#cabb9e] bg-[#f4eddf] px-3 py-1 text-xs font-medium">A동 · 창고 구역</span></div>
          <div className="mt-6 overflow-hidden rounded-xl border border-[#cfc4b0] bg-[#d8d1c2] p-3"><div className="relative aspect-[16/8] rounded-lg bg-[linear-gradient(150deg,#777063_0%,#a89d8a_41%,#514c43_42%,#514c43_100%)]"><div className="absolute left-[12%] top-[17%] h-[54%] w-[34%] border-2 border-[#c65a37] bg-[#d97745]/15" /><div className="absolute bottom-5 left-5 rounded bg-[#2f2a22]/80 px-2 py-1 text-xs text-white">A-03 · 스냅샷 10:31</div><div className="absolute right-4 top-4 rounded bg-[#fbf8f1]/90 px-2 py-1 text-xs">제한 구역 / 3개 점</div></div></div>
          <div className="mt-5 grid gap-3 sm:grid-cols-3"><ChecklistCard icon={Camera} label="영상 수신" value="확인됨" state="ready" /><ChecklistCard icon={MapPin} label="제한 구역" value="저장 전" state="attention" /><ChecklistCard icon={ShieldAlert} label="감지 설정" value="2개 보류" state="attention" /></div>
        </main>
        <aside className="rounded-xl border border-[#d7cfbf] bg-[#fbf8f1] p-4"><p className="text-[11px] font-medium tracking-[0.12em] text-[#8b7d68]">CAMERA A-03</p><h3 className="mt-2 font-semibold">창고 남측 출입로</h3><p className="mt-1 text-sm text-[#776c5a]">ONVIF · H.264 / 1080p</p><div className="mt-5 space-y-2"><FieldRow label="연결 검사" value="통과" /><FieldRow label="제한 구역" value="필요" emphasis /><FieldRow label="지게차 감지" value="활성" /><FieldRow label="재연결 검사" value="미완료" /></div><button className="mt-6 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-[#363027] px-3 text-sm font-medium text-white">영역 저장 후 감지 활성화 <ChevronRight className="size-4" /></button></aside>
      </div>
      <MobileFieldbook />
    </MockupFrame>
  );
}

function PanoramaMockup() {
  return (
    <MockupFrame label="03 / PANORAMA" title="현장 전체를 한 번에 읽기" note="라이브 월과 공간적 상태 인지를 중심에 두고, 사건은 카메라 맥락 안에서 드러냅니다." tone="blue">
      <div className="grid min-h-[580px] bg-[#0c1720] text-[#e6f1f6] lg:grid-cols-[minmax(0,1fr)_320px]">
        <main className="p-4 sm:p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-lg bg-cyan-300 text-[#06202b]"><Eye className="size-5" /></div><div><h2 className="font-semibold">한빛 물류센터 / A동</h2><p className="text-xs text-[#8ea7b4]">16대 중 12대 수신 · 최근 갱신 4초 전</p></div></div><div className="flex items-center gap-2"><span className="rounded-full border border-cyan-300/30 bg-cyan-300/10 px-3 py-1 text-xs text-cyan-200">라이브 월</span><span className="rounded-full bg-red-400 px-3 py-1 text-xs font-medium text-[#251012]">미처리 3</span></div></div>
          <div className="mt-5 grid gap-2 sm:grid-cols-2 xl:grid-cols-3"><CameraTile name="A-01 출입구" state="정상 수신" scene="gate" /><CameraTile name="A-03 창고 남측" state="긴급 사건" scene="warehouse" critical /><CameraTile name="A-04 적재 구역" state="정상 수신" scene="loading" /><CameraTile name="B-02 조립 라인" state="영상 지연" scene="line" warning /><CameraTile name="B-07 작업장" state="고위험 감지" scene="work" critical /><CameraTile name="B-09 통로" state="첫 프레임 대기" scene="hall" warning /></div>
        </main>
        <aside className="border-t border-white/10 bg-[#10212b] p-5 lg:border-l lg:border-t-0"><p className="text-[11px] font-medium tracking-[0.14em] text-[#819ba8]">SITUATION BOARD</p><h3 className="mt-2 text-lg font-semibold">A동 위험 구역</h3><div className="relative mt-4 aspect-square overflow-hidden rounded-xl border border-white/10 bg-[#18313d]"><div className="absolute left-[17%] top-[18%] h-[23%] w-[27%] rounded border border-cyan-200/50" /><div className="absolute right-[15%] top-[25%] h-[25%] w-[20%] rounded border border-white/20" /><div className="absolute bottom-[16%] left-[28%] h-[22%] w-[49%] rounded border border-white/20" /><span className="absolute left-[26%] top-[30%] size-3 rounded-full bg-cyan-300 shadow-[0_0_0_5px_rgb(103_232_249_/_0.12)]" /><span className="absolute bottom-[27%] left-[49%] size-3 rounded-full bg-red-400 shadow-[0_0_0_5px_rgb(248_113_113_/_0.13)]" /><span className="absolute right-[23%] top-[37%] size-3 rounded-full bg-amber-300" /></div><div className="mt-5 space-y-3"><SituationItem color="bg-red-400" title="창고 A-03" text="제한 구역 진입 · 신규" /><SituationItem color="bg-amber-300" title="작업장 B-07" text="보호구 확인 필요" /><SituationItem color="bg-cyan-300" title="출입구 A-01" text="정상 수신" /></div><button className="mt-6 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-cyan-200/30 bg-cyan-300/10 text-sm font-medium text-cyan-100">공간 기준으로 사건 보기 <ArrowRight className="size-4" /></button></aside>
      </div>
      <MobilePanorama />
    </MockupFrame>
  );
}

function MockupFrame({ label, title, note, tone, children }: { label: string; title: string; note: string; tone: "dark" | "paper" | "blue"; children: React.ReactNode }) {
  const border = tone === "paper" ? "border-[#d7cfbf]" : tone === "blue" ? "border-[#24404d]" : "border-zinc-700";
  return <div className={`overflow-hidden rounded-2xl border ${border} shadow-[0_24px_70px_rgb(0_0_0_/_0.12)]`}><div className={`flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4 ${tone === "paper" ? "border-[#d7cfbf] bg-[#fbf8f1] text-[#27241f]" : tone === "blue" ? "border-[#24404d] bg-[#10212b] text-[#e6f1f6]" : "border-zinc-700 bg-[#16191c] text-zinc-100"}`}><div><p className="font-mono text-[10px] tracking-[0.14em] opacity-60">{label}</p><h2 className="mt-1 font-semibold">{title}</h2></div><p className="max-w-xl text-sm opacity-65">{note}</p></div>{children}</div>;
}

function SignalBrand() { return <div className="flex items-center gap-2"><div className="grid size-8 place-items-center rounded-lg bg-red-500 text-white"><Activity className="size-4" /></div><div><p className="text-sm font-semibold text-zinc-100">KSI / SIGNAL</p><p className="text-[10px] text-zinc-500">HANBIT LOGISTICS</p></div></div>; }
function IncidentRow({ active = false, title, camera, level }: { active?: boolean; title: string; camera: string; level: string }) { return <div className={`rounded-lg border p-3 ${active ? "border-red-400/60 bg-red-400/10" : "border-white/5 bg-white/[0.03]"}`}><div className="flex items-center justify-between gap-2"><p className="text-sm font-medium text-zinc-100">{title}</p><span className={`text-[10px] ${active ? "text-red-300" : "text-zinc-500"}`}>{level}</span></div><p className="mt-1 text-xs text-zinc-500">{camera}</p></div>; }
function Stat({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "ready" | "attention" | "neutral" }) { const valueColor = tone === "ready" ? "text-emerald-300" : tone === "attention" ? "text-amber-200" : "text-zinc-100"; return <div className="rounded-lg border border-white/10 bg-white/[0.04] p-3"><p className="text-xs text-zinc-500">{label}</p><p className={`mt-1 font-semibold ${valueColor}`}>{value}</p></div>; }
function ActionButton({ label, primary = false }: { label: string; primary?: boolean }) { return <button className={`flex min-h-11 w-full items-center justify-between rounded-lg px-3 text-sm font-medium ${primary ? "bg-red-500 text-white" : "border border-white/10 bg-white/[0.04] text-zinc-200"}`}>{label}<ChevronRight className="size-4" /></button>; }
function SetupStep({ label, complete = false, current = false }: { label: string; complete?: boolean; current?: boolean }) { return <li className={`flex items-center gap-3 rounded-lg px-3 py-2.5 ${current ? "bg-[#ede5d7] font-medium" : ""}`}><span className={`grid size-5 place-items-center rounded-full text-[10px] ${complete ? "bg-[#556b48] text-white" : current ? "border-2 border-[#a44b31] text-[#a44b31]" : "border border-[#bfb3a0] text-[#8b7d68]"}`}>{complete ? "✓" : current ? "3" : ""}</span>{label}</li>; }
function ChecklistCard({ icon: Icon, label, value, state }: { icon: typeof Camera; label: string; value: string; state: "ready" | "attention" }) { return <div className="rounded-lg border border-[#d7cfbf] p-3"><Icon className={`size-4 ${state === "ready" ? "text-[#556b48]" : "text-[#b34b2d]"}`} /><p className="mt-3 text-xs text-[#776c5a]">{label}</p><p className="mt-1 font-medium">{value}</p></div>; }
function FieldRow({ label, value, emphasis = false }: { label: string; value: string; emphasis?: boolean }) { return <div className="flex items-center justify-between border-b border-[#e5ded0] py-2 text-sm"><span className="text-[#776c5a]">{label}</span><span className={emphasis ? "font-medium text-[#b34b2d]" : "font-medium"}>{value}</span></div>; }
function CameraTile({ name, state, scene, critical = false, warning = false }: { name: string; state: string; scene: string; critical?: boolean; warning?: boolean }) { const gradient = scene === "gate" ? "from-[#718090] via-[#354250] to-[#121a22]" : scene === "warehouse" ? "from-[#525c67] via-[#2c333a] to-[#11161d]" : scene === "loading" ? "from-[#4c6670] via-[#283a42] to-[#101820]" : scene === "line" ? "from-[#56505d] via-[#312d38] to-[#15131b]" : scene === "work" ? "from-[#745a4d] via-[#3c322f] to-[#191615]" : "from-[#56616b] via-[#29333b] to-[#11171b]"; return <div className={`relative aspect-video overflow-hidden rounded-lg border bg-gradient-to-br ${gradient} ${critical ? "border-red-400/70" : warning ? "border-amber-300/60" : "border-white/10"}`}><div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/60 to-transparent p-2 text-[10px]"><span>{name}</span><span className={critical ? "text-red-300" : warning ? "text-amber-200" : "text-cyan-100"}>{state}</span></div><div className="absolute bottom-2 left-2 flex items-center gap-1 text-[10px] text-white/70"><Camera className="size-3" />12 FPS</div>{critical && <div className="absolute bottom-[23%] left-[34%] size-[23%] border-2 border-red-300" />}</div>; }
function SituationItem({ color, title, text }: { color: string; title: string; text: string }) { return <div className="flex items-center gap-3"><span className={`size-2.5 rounded-full ${color}`} /><div><p className="text-sm font-medium">{title}</p><p className="text-xs text-[#8ea7b4]">{text}</p></div></div>; }
function MobileSignal() { return <div className="border-t border-white/10 bg-[#0d0f11] p-5"><p className="text-xs font-medium tracking-[0.12em] text-zinc-500">MOBILE RESPONSE / 390px</p><div className="mt-3 max-w-sm rounded-[2rem] border border-zinc-700 bg-[#181b1e] p-4 text-zinc-100"><div className="flex items-center justify-between"><span className="text-sm font-semibold">지게차 접근 감지</span><span className="rounded-full bg-red-500 px-2 py-1 text-[10px]">신규</span></div><p className="mt-2 text-xs text-zinc-400">창고 A-03 · 10:31</p><button className="mt-4 min-h-11 w-full rounded-lg bg-red-500 text-sm font-medium">사건 확인</button></div></div>; }
function MobileFieldbook() { return <div className="border-t border-[#d7cfbf] bg-[#f4f0e8] p-5"><p className="text-xs font-medium tracking-[0.12em] text-[#8b7d68]">MOBILE INSTALLER / 390px</p><div className="mt-3 max-w-sm rounded-[2rem] border border-[#d7cfbf] bg-[#fbf8f1] p-4 text-[#27241f]"><p className="text-xs text-[#8b7d68]">3 / 5 단계</p><p className="mt-1 font-semibold">A-03 제한 구역 설정</p><p className="mt-2 text-xs text-[#776c5a]">영역 저장 후 제한 구역 감지를 활성화할 수 있습니다.</p><button className="mt-4 min-h-11 w-full rounded-lg bg-[#363027] text-sm font-medium text-white">영역 편집 열기</button></div></div>; }
function MobilePanorama() { return <div className="border-t border-[#24404d] bg-[#0c1720] p-5"><p className="text-xs font-medium tracking-[0.12em] text-[#819ba8]">MOBILE SITUATION / 390px</p><div className="mt-3 max-w-sm rounded-[2rem] border border-[#24404d] bg-[#10212b] p-4 text-[#e6f1f6]"><div className="flex items-center justify-between"><span className="font-semibold">A동 위험 구역</span><span className="text-xs text-red-300">미처리 3</span></div><div className="mt-3 flex gap-2"><span className="size-3 rounded-full bg-red-400" /><p className="text-sm">A-03 제한 구역 진입</p></div><button className="mt-4 min-h-11 w-full rounded-lg border border-cyan-200/30 bg-cyan-300/10 text-sm font-medium text-cyan-100">공간 기준으로 보기</button></div></div>; }
