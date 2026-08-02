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
	// This page has no dark variant — there is no prefers-color-scheme rule
	// anywhere in its CSS, and it renders warm paper in every system theme.
	// Claiming "dark light" invited the browser to draw form controls and
	// scrollbars dark on a permanently light page.
	colorScheme: "light",
	// Tints the mobile browser's address bar. It sits directly above the sticky
	// header, which is rgb(244 239 230 / 92%), so it matches the paper rather
	// than the dark product canvas further down the page.
	themeColor: "#f4efe6",
};

export default function RootLayout({ children }: { children: ReactNode }) {
	return (
		<html lang="en">
			<body>{children}</body>
		</html>
	);
}
