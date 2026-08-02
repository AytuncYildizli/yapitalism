import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
	metadataBase: new URL("https://yapitalism.com"),
	title: "Yapitalism",
	description: "Control coding agents by voice.",
};

export const viewport: Viewport = {
	width: "device-width",
	initialScale: 1,
	// The brand surface is dark in every system theme; there is no
	// prefers-color-scheme rule in its CSS.
	colorScheme: "dark",
	// Tints the mobile browser's address bar. Matched to --ink, the page
	// background sitting directly beneath it.
	themeColor: "#10110e",
};

export default function RootLayout({ children }: { children: ReactNode }) {
	return (
		<html lang="en">
			<body>{children}</body>
		</html>
	);
}
