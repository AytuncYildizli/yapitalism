import { ImageResponse } from "next/og";

export const alt = "Yapitalism — one mic, infinite interns";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// This site is `output: "export"`, so the card is rendered once at build time
// rather than per request.
export const dynamic = "force-static";

// Matched to the brand surface in globals.css.
const INK = "#10110e";
const ACID = "#e7ff2f";
const RED = "#ff4d35";

export default function Image() {
	return new ImageResponse(
		<div
			style={{
				display: "flex",
				flexDirection: "column",
				justifyContent: "space-between",
				width: "100%",
				height: "100%",
				padding: "60px 68px",
				background: INK,
				color: "#f5f6ea",
				// Impact is not available to the renderer; a heavy sans with tight
				// tracking is the closest approximation of the display face.
				fontFamily: "Arial, sans-serif",
			}}
		>
			<div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
				<div style={{ display: "flex", fontSize: "26px", fontWeight: 900, letterSpacing: "1px" }}>
					YAPITALISM™
				</div>
				<div
					style={{
						display: "flex",
						padding: "9px 13px",
						border: `1px solid ${ACID}`,
						background: "#171912",
						color: ACID,
						fontSize: "15px",
						fontWeight: 700,
						letterSpacing: "1.4px",
					}}
				>
					SPEAK → ROUTE → CONTROL
				</div>
			</div>

			<div style={{ display: "flex", flexDirection: "column" }}>
				<div
					style={{
						display: "flex",
						alignSelf: "flex-start",
						marginBottom: "22px",
						padding: "8px 12px",
						background: ACID,
						color: "#080907",
						fontSize: "17px",
						fontWeight: 900,
						letterSpacing: "1.6px",
					}}
				>
					VOICE CONTROL FOR CODING AGENTS
				</div>
				<div
					style={{
						display: "flex",
						fontSize: "104px",
						fontWeight: 900,
						letterSpacing: "-4px",
						lineHeight: 0.86,
					}}
				>
					ONE MIC.
				</div>
				<div style={{ display: "flex", fontSize: "104px", fontWeight: 900, letterSpacing: "-4px", lineHeight: 0.86 }}>
					INFINITE{" "}
					<span style={{ color: ACID, marginLeft: "22px" }}>INTERNS.</span>
				</div>
				<div
					style={{
						display: "flex",
						marginTop: "26px",
						fontSize: "34px",
						fontWeight: 900,
						letterSpacing: "-0.5px",
					}}
				>
					TURN YAPPING INTO SHIPPING.
				</div>
			</div>

			<div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
				<div style={{ display: "flex", color: "#b7bcaa", fontSize: "18px", fontWeight: 700 }}>
					Codex · Claude · Kimi — from the voice app you already use
				</div>
				<div style={{ display: "flex", color: RED, fontSize: "16px", fontWeight: 900, letterSpacing: "1.2px" }}>
					yapitalism.com
				</div>
			</div>
		</div>,
		size,
	);
}
