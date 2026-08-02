"use client";

import { useState } from "react";

// Where "request private beta" goes. This is a standalone static export, so the
// Superset marketing app's /contact route does not exist here.
const BETA_HREF = "https://github.com/AytuncYildizli/yapitalism";

type Scope = "solo" | "crew" | "fleet";

const MODES: Record<
	Scope,
	{ role: string; decision: string; user: string; system: string }
> = {
	solo: {
		role: "ONE AGENT",
		decision:
			"Direct mode lets you launch and keep controlling one selected coding-agent session by voice.",
		user: "“Open the checkout workspace. Start Codex on the login bug.”",
		system:
			"SPOKEN TARGET RESOLVED → CODEX SESSION LAUNCHED → TERMINAL READY FOR FOLLOW-UP",
	},
	crew: {
		role: "THE CREW",
		decision:
			"Goblin Mode turns one voice conversation into a command channel for multiple live coding-agent sessions.",
		user: "“Codex fixes checkout. Claude reviews the tests. Keep both sessions open.”",
		system:
			"TARGETS RESOLVED → AGENT SESSIONS LAUNCHED → LIVE TERMINALS AVAILABLE FOR FOLLOW-UP",
	},
	fleet: {
		role: "THE FLEET",
		decision:
			"Fleet mode extends voice control across hosts, workspaces, live agents and scheduled automations.",
		user: "“Run the release audit on Studio. Pause the nightly automation after this pass.”",
		system: "HOST + WORKSPACE RESOLVED → AUTOMATION RUN → PAUSE ACTION PREPARED",
	},
};

const PERMITS: {
	scope: Scope;
	num: string;
	name: string;
	tag: string;
	outcome: string;
	boundary: string;
	action: string;
	recommended?: boolean;
	hazard?: boolean;
}[] = [
	{
		scope: "solo",
		num: "COMMAND MODE 01",
		name: "ONE AGENT",
		tag: "DIRECT · SINGLE SESSION",
		outcome:
			"Pick Codex, Claude or another configured agent. Launch it in a workspace, give it the task and keep talking into the same live session.",
		boundary:
			"choose the agent · start the job · send the next instruction · ask what happened.",
		action: "CONTROL ONE AGENT",
	},
	{
		scope: "crew",
		num: "COMMAND MODE 02",
		name: "THE CREW",
		tag: "MULTI-AGENT · GOBLIN MODE",
		outcome:
			"Start multiple coding agents, split the work and address each live terminal while they run. One conversation becomes the command channel for the crew.",
		boundary:
			"assign work · switch targets · send follow-ups · read live session state.",
		action: "RUN THE CREW",
		recommended: true,
	},
	{
		scope: "fleet",
		num: "COMMAND MODE 03",
		name: "THE FLEET",
		tag: "HOSTS · WORKSPACES · AUTOMATIONS",
		outcome:
			"Choose the machine and workspace, launch agents and run, pause or resume recurring automations. High-impact operations still stop at their approval gates.",
		boundary:
			"resolve the target · operate sessions · control automations · keep dangerous actions gated.",
		action: "COMMAND THE FLEET",
		hazard: true,
	},
];

const TICKER = [
	"TALK TO ONE AGENT",
	"RUN THE WHOLE CREW",
	"SEND FOLLOW-UPS WHILE THEY WORK",
	"ONE MIC. INFINITE INTERNS.",
];

const STEPS = [
	{
		num: "01",
		title: "KEEP THE VOICE SURFACE",
		body: "Use the GPT/Codex Voice app you already know. Yapitalism does not replace it.",
	},
	{
		num: "02",
		title: "CONNECT THE SUPERSET MCP ADAPTER",
		body: "It exposes agents, tasks, workspaces, terminals and automations to the conversation.",
	},
	{
		num: "03",
		title: "KEEP COMMANDING WHILE THEY WORK",
		body: "Launch sessions, send follow-ups, inspect status and pause or resume automations by voice. RelayProof supports long-running delivery in the background.",
	},
];

export function YapitalismPage() {
	const [scope, setScope] = useState<Scope>("crew");
	const mode = MODES[scope];

	return (
		<>
			<header className="shell top">
				<div className="wordmark">YAPITALISM™</div>
				<div className="proof-pill micro">SPEAK → ROUTE → CONTROL</div>
			</header>

			<main>
				<section className="shell hero">
					<div>
						<span className="eyebrow micro">VOICE CONTROL FOR CODING AGENTS</span>
						<h1>
							ONE MIC.
							<br />
							INFINITE
							<br />
							<em>INTERNS.</em>
						</h1>
						<p className="promise">Turn yapping into shipping.</p>
						<p className="lede">
							Talk to the GPT/Codex Voice app you already use. Yapitalism lets you
							choose an agent, launch work, send follow-ups into live sessions and
							control the whole agent fleet without leaving the conversation.
						</p>
						<div className="actions">
							{/* Anchors rather than scripted scrolling: html has
							    scroll-behavior:smooth, so these work before hydration and
							    without JavaScript at all. */}
							<div className="action-wrap">
								<a className="cta primary" href="#permits">
									<span>TAKE THE MIC</span>
									<span>↓</span>
								</a>
								<span className="explain">
									Choose one agent, a working crew or the full fleet.
								</span>
							</div>
							<div className="action-wrap">
								<a className="cta secondary" href="#voice-loop">
									<span>SEE THE VOICE LOOP</span>
									<span>↘</span>
								</a>
								<span className="explain">
									One spoken command → selected targets → live agent sessions.
								</span>
							</div>
						</div>
					</div>
					<aside
						className="poster-wrap"
						aria-label="Subscription Goblin campaign poster"
					>
						{/* Plain <img>: this is a static export with no image optimiser, and
						    the source has already been resized from 2048px/10.3MB. */}
						<img
							alt="Yapitalism Subscription Goblin campaign poster"
							height={1024}
							src="/subscription-goblin.webp"
							width={1024}
						/>
						<div className="poster-cap">
							<span>THE SUBSCRIPTION GOBLIN</span>
							<span>EMPLOYEE #000∞</span>
						</div>
					</aside>
				</section>

				<div aria-hidden="true" className="ticker">
					<div className="ticker-track">
						{/* Duplicated once: the marquee keyframe translates -50%, so the
						    second copy is what makes the loop seamless. */}
						{[...TICKER, ...TICKER].map((phrase, i) => (
							<span key={`${phrase}-${i}`}>{phrase}</span>
						))}
					</div>
				</div>

				<section className="permits" id="permits">
					<div className="shell">
						<div className="section-kicker micro">
							VOICE COMMAND MODES · PICK ONE
						</div>
						<h2 className="section-title">
							HOW MANY AGENTS DO YOU WANT ON THE MIC?
						</h2>
						<p className="section-sub">
							This is the actual product. Start one coding agent, command several
							live sessions or operate hosts, workspaces and automations through
							the same voice conversation.
						</p>

						<div
							aria-label="Choose a Yapitalism voice control mode"
							className="permit-grid"
							role="group"
						>
							{PERMITS.map((permit) => (
								<article
									className={`permit${permit.recommended ? " recommended" : ""}`}
									key={permit.scope}
								>
									<button
										aria-pressed={scope === permit.scope}
										className={`permit-choice${permit.hazard ? " hazard" : ""}`}
										onClick={() => setScope(permit.scope)}
										type="button"
									>
										<span className="permit-num">{permit.num}</span>
										<h3 className="permit-name">{permit.name}</h3>
										<span className="scope">{permit.tag}</span>
										<p className="outcome">{permit.outcome}</p>
										<div className="boundary">
											<strong>BY VOICE</strong>
											{permit.boundary}
										</div>
										<div className="hire-line">
											<span>{permit.action}</span>
											<span>→</span>
										</div>
									</button>
								</article>
							))}
						</div>

						<div className="receipt-zone" id="voice-loop">
							<div className="decision">
								<div className="micro section-kicker">
									THE CONTROL SURFACE IS THE CONVERSATION
								</div>
								<h3>
									YAP.
									<br />
									<span>{mode.role}</span> MOVES.
								</h3>
								<p>{mode.decision}</p>
								<p className="plain-truth">
									You are buying voice control over the agent runtime—not another
									voice client and not a receipt dashboard.
								</p>
								<a className="cta primary" href="#quickstart">
									<span>CONNECT THE AGENTS</span>
									<span>↓</span>
								</a>
								<span className="explain">
									Keep your current Voice app. Add the Yapitalism control layer
									behind it.
								</span>
							</div>

							{/* Keyed on scope so React remounts it on change, which restarts the
							    stamp-in animation. The original did this by removing the class
							    and forcing a reflow. */}
							<div
								aria-live="polite"
								className="receipt voice-console stamp-in"
								key={scope}
							>
								<div className="receipt-head">
									<span>YAPITALISM · LIVE VOICE LOOP</span>
									<span>{scope.toUpperCase()}</span>
								</div>
								<div className="voice-turn">
									<span className="voice-label">YOU · VOICE</span>
									<div className="voice-speech">{mode.user}</div>
								</div>
								<div className="voice-turn">
									<span className="voice-label">YAPITALISM · CONTROL</span>
									<div className="voice-system">{mode.system}</div>
								</div>
								<div className="voice-turn">
									<span className="voice-label">YOU · FOLLOW-UP</span>
									<div className="voice-speech">
										“Tell Codex to keep going. What did Claude find?”
									</div>
								</div>
								<div className="voice-foot">
									ONE MIC. MULTIPLE LIVE AGENTS. SAME CONVERSATION.
								</div>
							</div>
						</div>
					</div>
				</section>

				<section className="quickstart shell" id="quickstart">
					<div className="quick-grid">
						<div>
							<div className="micro section-kicker">NO NEW VOICE APP</div>
							<h2>PUT YOUR AGENTS ON THE MIC.</h2>
						</div>
						<div className="steps">
							{STEPS.map((step) => (
								<div className="step" key={step.num}>
									<div className="step-num">{step.num}</div>
									<div>
										<b>{step.title}</b>
										<p>{step.body}</p>
									</div>
								</div>
							))}
							<a
								className="beta-cta"
								href={BETA_HREF}
								rel="noreferrer"
								target="_blank"
							>
								REQUEST PRIVATE BETA · {scope.toUpperCase()} MODE →
							</a>
							<span className="explain">
								Public enrollment endpoint connects after compatibility and scope
								gates pass.
							</span>
						</div>
					</div>
				</section>
			</main>

			<footer className="shell">
				<div className="footer-row micro">
					<span>YAPITALISM · PARODY PRODUCT CONCEPT</span>
					<span>NOT AFFILIATED WITH OR ENDORSED BY OPENAI.</span>
				</div>
			</footer>

			<div className="sticky-choice">
				<span>
					VOICE MODE: <b>{scope.toUpperCase()}</b>
				</span>
				<a href="#quickstart">CONTINUE →</a>
			</div>
		</>
	);
}
