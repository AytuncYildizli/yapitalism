import { ImageResponse } from "next/og";

export const alt = "Yapitalism controls coding agents from GPT and Codex Voice";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// This site is `output: "export"`, so the card has to be rendered once at build
// time rather than per request. Without this the export fails outright — which
// is preferable to the previous state, where the page advertised
// twitter:card=summary_large_image and served no image at all.
export const dynamic = "force-static";

export default function Image() {
	return new ImageResponse(
		<div
			style={{
				display: "flex",
				flexDirection: "column",
				justifyContent: "space-between",
				width: "100%",
				height: "100%",
				padding: "64px 72px",
				background: "#f4efe6",
				color: "#1b1d18",
				fontFamily: "Arial, sans-serif",
			}}
		>
			<div style={{ display: "flex", alignItems: "center", gap: "18px" }}>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "center",
						width: "54px",
						height: "54px",
						borderRadius: "10px",
						background: "#1b1d18",
						color: "#b9e63b",
						fontSize: "24px",
						fontWeight: 700,
					}}
				>
					Y
				</div>
				<div style={{ display: "flex", fontSize: "34px", fontWeight: 700 }}>
					Yapitalism
				</div>
			</div>

			<div style={{ display: "flex", gap: "54px", alignItems: "flex-end" }}>
				<div style={{ display: "flex", flex: 1, flexDirection: "column" }}>
					<div
						style={{
							display: "flex",
							maxWidth: "700px",
							fontSize: "72px",
							fontWeight: 600,
							letterSpacing: "-4px",
							lineHeight: 0.96,
						}}
					>
						Tell the agents what to do.
					</div>
					<div
						style={{
							display: "flex",
							marginTop: "26px",
							color: "#5d6157",
							fontSize: "28px",
						}}
					>
						Keep talking while they do it.
					</div>
				</div>

				<div
					style={{
						display: "flex",
						flexDirection: "column",
						gap: "14px",
						width: "330px",
						padding: "24px",
						borderRadius: "18px",
						background: "#11150f",
						color: "#f2efe6",
						fontSize: "18px",
					}}
				>
					<div style={{ display: "flex", color: "#b9e63b", fontSize: "15px" }}>
						GPT Voice connected
					</div>
					<div style={{ display: "flex", justifyContent: "space-between" }}>
						<span>Codex</span>
						<span style={{ color: "#b9e63b" }}>running</span>
					</div>
					<div style={{ display: "flex", justifyContent: "space-between" }}>
						<span>Claude</span>
						<span style={{ color: "#b9e63b" }}>reviewing</span>
					</div>
					<div style={{ display: "flex", justifyContent: "space-between" }}>
						<span>Kimi</span>
						<span style={{ color: "#b9e63b" }}>mapped</span>
					</div>
				</div>
			</div>
		</div>,
		size,
	);
}
