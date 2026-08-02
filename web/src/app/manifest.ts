import type { MetadataRoute } from "next";

// The fork serves this from a route handler (`export function GET()`), which is
// a server response and cannot exist under `output: "export"`. Next's
// `manifest.ts` convention emits a real static manifest.webmanifest at build
// time instead, and wires the <link rel="manifest"> itself.
//
// The icon path also differs from the fork's: this app ships the mark through
// the App Router `icon.svg` convention, so it is served at /icon.svg rather
// than /yapitalism-icon.svg. Copying the fork's path verbatim would point the
// manifest at a 404.
export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
	return {
		name: "Yapitalism",
		short_name: "Yapitalism",
		description: "Control live coding-agent sessions from GPT or Codex Voice.",
		start_url: "/",
		display: "standalone",
		background_color: "#f4efe6",
		// The fork uses the dark canvas colour (#11150f) here. An installed app
		// opens at the top of this page, which is paper, so a dark title bar
		// would sit directly above a cream header. Matched to what renders.
		theme_color: "#f4efe6",
		icons: [
			{
				src: "/icon.svg",
				sizes: "any",
				type: "image/svg+xml",
				purpose: "any",
			},
		],
	};
}
