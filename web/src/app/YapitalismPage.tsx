import { ArrowDown, ArrowRight, Mic2 } from "lucide-react";
import styles from "./yapitalism.module.css";

// Where "try the private beta" goes. This page is a standalone static export,
// so the Superset marketing app's /contact route does not exist here — pointing
// at it shipped two dead CTAs on the live domain. Kept in one place so the
// destination can change without hunting through the markup.
const BETA_HREF = "https://github.com/AytuncYildizli/yapitalism";

const SESSIONS = [
	{
		agent: "Codex",
		state: "Running",
		task: "Fixing checkout session refresh",
		workspace: "checkout",
		branch: "fix/checkout-session",
		file: "apps/web/src/auth/session.ts",
		check: "auth tests 12/12",
	},
	{
		agent: "Claude",
		state: "Reviewing",
		task: "Checking the Codex diff",
		workspace: "checkout review",
		branch: "review/482",
		file: "apps/web/src/auth/session.test.ts",
		check: "1 concern: retry path",
	},
	{
		agent: "Kimi",
		state: "Mapped",
		task: "Tracing the schema migration",
		workspace: "migration",
		branch: "map/schema-v3",
		file: "packages/db/src/schema",
		check: "14 tables indexed",
	},
];

const WAVEFORM = [
	28, 44, 72, 38, 60, 82, 46, 68, 34, 56, 76, 42, 64, 36, 70, 48, 58, 32,
].map((height, position) => ({ id: `wave-${position + 1}`, height }));

export function YapitalismPage() {
	return (
		<main className={styles.yapitalism}>
			<section className={styles.hero}>
				<div className={styles.heroCopy}>
					<p className={styles.eyebrow}>Yapitalism for GPT and Codex Voice</p>
					<h1>
						Tell the agents what to do.
						<em>Keep talking while they do it.</em>
					</h1>
					<p className={styles.lede}>
						Start Codex and keep talking. Redirect Claude or ask Kimi what
						changed without switching tabs.
					</p>
					<div className={styles.actions}>
						<a
							className={styles.primaryAction}
							href={BETA_HREF}
							rel="noreferrer"
							target="_blank"
						>
							Try the private beta <ArrowRight aria-hidden="true" size={18} />
						</a>
						<a className={styles.secondaryAction} href="#product-demo">
							See it work <ArrowDown aria-hidden="true" size={17} />
						</a>
					</div>
				</div>

				<article
					aria-label="A GPT Voice command opening three coding agent sessions"
					className={styles.productCanvas}
					id="product-demo"
				>
					<header className={styles.canvasHeader}>
						<div>
							<span className={styles.canvasWordmark}>Yapitalism</span>
							<span className={styles.connected}>
								<i aria-hidden="true" /> Connected
							</span>
						</div>
						<span>GPT Voice · Superset host: studio</span>
					</header>

					<div className={styles.canvasGrid}>
						<section
							aria-label="Voice conversation"
							className={styles.voicePane}
						>
							<div className={styles.voiceSource}>
								<div className={styles.micBadge}>
									<Mic2 aria-hidden="true" size={20} strokeWidth={1.7} />
								</div>
								<div>
									<strong>GPT Voice</strong>
									<span>Listening</span>
								</div>
							</div>

							<blockquote>
								“Codex, fix checkout. Claude, review the diff. Kimi, map the
								migration.”
							</blockquote>

							<div className={styles.waveform} aria-hidden="true">
								{WAVEFORM.map((bar, index) => (
									<i
										key={bar.id}
										style={{
											height: `${bar.height}%`,
											animationDelay: `${index * -55}ms`,
										}}
									/>
								))}
							</div>

							<div className={styles.resolution}>
								<span className={styles.resolutionMark}>Y</span>
								<div>
									<span>Yapitalism matched</span>
									<strong>checkout · diff review · migration</strong>
								</div>
							</div>
						</section>

						<section
							aria-label="Resolved coding agent sessions"
							className={styles.sessionLedger}
						>
							<header className={styles.ledgerHeader}>
								<div>
									<span>Resolved sessions</span>
									<strong>3 live</strong>
								</div>
								<span>studio / 3 workspaces</span>
							</header>

							{SESSIONS.map((session) => (
								<article className={styles.sessionRow} key={session.agent}>
									<header>
										<strong>{session.agent}</strong>
										<span className={styles.sessionState}>
											<i aria-hidden="true" /> {session.state}
										</span>
									</header>
									<p>{session.task}</p>
									<dl className={styles.sessionMeta}>
										<div>
											<dt>workspace</dt>
											<dd>{session.workspace}</dd>
										</div>
										<div>
											<dt>branch</dt>
											<dd>
												<code>{session.branch}</code>
											</dd>
										</div>
									</dl>
									<div className={styles.workTruth}>
										<code>{session.file}</code>
										<span>check · {session.check}</span>
									</div>
								</article>
							))}
						</section>
					</div>

					<div className={styles.followUp}>
						<div>
							<span>You · GPT Voice</span>
							<strong>Same conversation</strong>
						</div>
						<blockquote>
							“Keep Codex going. Ask Claude if it’s safe.”
						</blockquote>
					</div>
				</article>
			</section>

			<section className={styles.conversionSection}>
				<div>
					<p className={styles.eyebrow}>Voice stays the interface</p>
					<h2>Talk to one agent or the whole fleet.</h2>
				</div>
				<div className={styles.conversionCopy}>
					<p>
						Yapitalism maps the name you say to a live Superset session. The
						next thing you say goes back to that session. Risky actions still
						wait for approval.
					</p>
					<a
						className={styles.primaryAction}
						href={BETA_HREF}
						rel="noreferrer"
						target="_blank"
					>
						Try the private beta <ArrowRight aria-hidden="true" size={18} />
					</a>
					<p className={styles.brandNote}>
						<strong>The names, briefly.</strong> Subscription Goblin is the
						mascot. Goblin Mode is the multi-agent mode. API at Home is the
						campaign. RelayProof keeps background delivery accountable. Superset
						runs the backend.
					</p>
				</div>
			</section>
		</main>
	);
}
