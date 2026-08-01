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
	// The page is read on a phone while walking; respect the system theme.
	colorScheme: "dark light",
};

export default function RootLayout({ children }: { children: ReactNode }) {
	return (
		<html lang="en">
			<body>{children}</body>
		</html>
	);
}
