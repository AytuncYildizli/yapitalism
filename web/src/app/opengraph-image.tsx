import { ImageResponse } from "next/og";

export const alt = "Yapitalism — turn yapping into shipping";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// This site is `output: "export"`, so the card is rendered once at build time.
export const dynamic = "force-static";

// Matched to the brand tokens in globals.css.
const PAPER = "#f3eedb";
const INK = "#10110e";
const ACID = "#e7ff2f";
const RED = "#ff4d35";

// Anton is not loaded here: ImageResponse needs a font buffer and the self-hosted
// files are woff2, which satori does not accept. The palette and the copy carry
// the card; a heavy system sans is close enough at this size.
export default function Image() {
	return new ImageResponse(
		<div
			style={{
				display: "flex",
				flexDirection: "column",
				justifyContent: "space-between",
				width: "100%",
				height: "100%",
				padding: "56px 64px",
				background: PAPER,
				color: INK,
				fontFamily: "Arial, sans-serif",
			}}
		>
			<div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
				<div style={{ display: "flex", alignItems: "baseline", fontSize: "34px", fontWeight: 900 }}>
					<span style={{ background: ACID, padding: "0 6px" }}>YAP</span>
					<span>ITALISM</span>
				</div>
				<div
					style={{
						display: "flex",
						border: `3px solid ${INK}`,
						borderRadius: "999px",
						padding: "8px 20px",
						fontSize: "17px",
						fontWeight: 700,
						letterSpacing: "1.4px",
					}}
				>
					PRIVATE BETA
				</div>
			</div>

			<div style={{ display: "flex", flexDirection: "column" }}>
				<div style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "20px" }}>
					<div style={{ display: "flex", width: "46px", height: "14px", background: RED, border: `3px solid ${INK}` }} />
					<div style={{ display: "flex", fontSize: "18px", fontWeight: 700, letterSpacing: "2px" }}>
						VOICE-OPERATED AGENT CAPITALISM
					</div>
				</div>
				<div style={{ display: "flex", fontSize: "116px", fontWeight: 900, letterSpacing: "-5px", lineHeight: 0.86 }}>
					TURN <span style={{ color: RED, marginLeft: "26px" }}>YAPPING</span>
				</div>
				<div style={{ display: "flex", fontSize: "116px", fontWeight: 900, letterSpacing: "-5px", lineHeight: 0.86 }}>
					INTO SHIPPING.
				</div>
			</div>

			<div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
				<div style={{ display: "flex", fontSize: "19px", fontWeight: 600, color: "#5d5f52", letterSpacing: "1px" }}>
					ONE MIC / MULTIPLE AGENTS / VERIFIED DELIVERY / ZERO NEW VOICE APP
				</div>
				<div style={{ display: "flex", fontSize: "19px", fontWeight: 900 }}>yapitalism.com</div>
			</div>
		</div>,
		size,
	);
}
