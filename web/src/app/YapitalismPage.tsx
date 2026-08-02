"use client";

import { useState } from "react";

// Where "take the mic" goes. This is a standalone static export, so the
// Superset marketing app's /contact route does not exist here.
const BETA_HREF = "https://github.com/AytuncYildizli/yapitalism";

// Kept in sync with the README's install section by hand. If these drift, the
// site teaches people a command that does not work.
const INSTALL_CMD = `pipx install yapitalism
yapitalism-mcp

codex mcp add yapitalism --url http://127.0.0.1:8792/mcp`;

const STDIO_CONFIG = `{
  "mcpServers": {
    "yapitalism": {
      "command": "yapitalism-mcp",
      "args": ["--stdio"]
    }
  }
}`;

const CREW = [
	{
		who: "Codex",
		state: "working",
		say: "“Fix checkout.”",
		log: [
			["branch", "fix/checkout-session"],
			["auth tests", "12/12"],
		],
	},
	{
		who: "Claude",
		state: "reviewing",
		say: "“Review the diff.”",
		log: [
			["branch", "review/482"],
			["flagged", "retry path"],
		],
	},
	{
		who: "Kimi",
		state: "mapping",
		say: "“Map the migration.”",
		log: [
			["branch", "map/schema-v3"],
			["indexed", "14 tables"],
		],
	},
];

const RECEIPT = [
	"yap received.",
	"intern dispatched. ×3",
	"receipt or it didn't happen.",
	"keep talking. they're working.",
];

const BEATS = [
	{
		title: "Yap",
		body: "Use the voice surface already sitting inside your subscription. No new client pretending to be the product.",
	},
	{
		title: "Command",
		body: "Name Codex, Claude, Kimi, one live terminal, several sessions, or the host you mean. Yapitalism resolves it.",
	},
	{
		title: "Keep talking",
		body: "Send follow-ups and inspect state while they run. Risky actions still hold until you answer.",
	},
];

export function YapitalismPage() {
	// Goblin Mode is the product's own metaphor for single-agent versus fleet,
	// so the switch is the page's one interactive idea: it changes what the page
	// is showing, not just how it looks. The prototype drove this by setting
	// data-goblin on <body>; here it is React state on a wrapper, because a
	// static export cannot own the body element.
	const [goblin, setGoblin] = useState(false);
	const mode = goblin ? "on" : "off";

	return (
		<div data-goblin={mode}>
			<div className="bar">
				<span className="mark">
					<i>YAP</i>ITALISM
				</span>
				<span className="push">turn yapping into shipping</span>
				{/* The prototype's "local concept · v0.2" tag was a dev artifact. */}
				<span className="tag">private beta</span>
			</div>

			<div className="wrap">
				<section className="hero">
					<div>
						<p className="eyebrow">voice-operated agent capitalism</p>
						<h1>
							Turn <span className="hit">yapping</span>
							<br />
							into shipping.
						</h1>
						<p className="sub">
							Talk to the voice app you already use. Yapitalism turns the
							conversation into a control surface for{" "}
							<mark>one agent, a crew, or the whole fleet</mark>.
						</p>
						<p className="spec">
							one mic / multiple agents / verified delivery / zero new voice app
						</p>
						<div className="acts">
							<a
								className="btn"
								href={BETA_HREF}
								rel="noreferrer"
								target="_blank"
							>
								Take the mic →
							</a>
							<a className="btn alt" href="#mode">
								See the voice loop ↓
							</a>
						</div>
					</div>
					<div className="card">
						<img
							alt="The Subscription Goblin shouting into a microphone while coding terminals run behind him"
							src="/goblin.svg"
						/>
						<div className="burst">We have API at home</div>
					</div>
				</section>
			</div>

			<section className="mode" id="mode">
				<div className="wrap modeIn">
					<div className="modeHead">
						<h2>
							How many agents do you
							<br />
							want on the mic?
						</h2>
						<div className="switch">
							<span className="state">
								{goblin ? "Ship it" : "Talk about it"}
							</span>
							<button
								aria-label="Goblin Mode"
								aria-pressed={goblin}
								onClick={() => setGoblin((v) => !v)}
								type="button"
							>
								<span className="knob" />
							</button>
							<span>goblin mode</span>
						</div>
					</div>

					<p className="entered">
						The Subscription Goblin has entered the terminal.
					</p>

					<div className="crew">
						{CREW.map((intern) => (
							<div className="intern" key={intern.who}>
								<div className="who">
									<span>{intern.who}</span>
									<em>{intern.state}</em>
								</div>
								<p>{intern.say}</p>
								<div className="log">
									{intern.log.map(([label, value], i) => (
										<span key={label}>
											{i > 0 && <br />}
											{label} <b>{value}</b>
										</span>
									))}
								</div>
							</div>
						))}
					</div>

					<div className="receipt">
						{RECEIPT.map((line) => (
							<div key={line}>{line}</div>
						))}
					</div>
				</div>
			</section>

			<div className="wrap">
				<section className="beats">
					{BEATS.map((beat) => (
						<div className="beat" key={beat.title}>
							<h3>
								<span>{beat.title}</span>
							</h3>
							<p>{beat.body}</p>
						</div>
					))}
				</section>
			</div>

			<section className="install" id="install">
				<div className="wrap installIn">
					<div className="installHead">
						<h2>
							It&apos;s a local <em>MCP server</em>.
						</h2>
						<p>
							Your voice client already speaks MCP. This runs on your machine, binds
							loopback only, and refuses any other host. No account, no relay, no
							terminal contents leaving the box.
						</p>
					</div>

					<div className="installCols">
						<div className="installCol">
							<span className="installStep">01 · install and run</span>
							<pre>
								<code>{INSTALL_CMD}</code>
							</pre>
						</div>
						<div className="installCol">
							<span className="installStep">
								02 · or let the client launch it
							</span>
							<pre>
								<code>{STDIO_CONFIG}</code>
							</pre>
							<span className="installNote">
								Claude Desktop, Cursor and the like spawn the server themselves
								over stdio.
							</span>
						</div>
					</div>

					<a className="btn alt" href={BETA_HREF} rel="noreferrer" target="_blank">
						Source and docs →
					</a>
				</div>
			</section>

			<section className="close">
				<div className="wrap closeIn">
					<h2>
						Your subscription
						<br />
						is now <em>management</em>.
					</h2>
					<p className="legal">
						Not affiliated with or endorsed by OpenAI. “API at Home” is campaign
						language, not an official API claim. Third-party product names are
						used descriptively. Blocked is a valid result. Fake green isn&apos;t.
					</p>
				</div>
			</section>
		</div>
	);
}
