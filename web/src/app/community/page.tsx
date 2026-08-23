// A page that states facts about something we did not make and do not control.
// The rule it lives under is the product's own: never claim more than you can
// prove. We can prove the token exists and who did not create it. Nothing else.
import type { Metadata } from "next";

export const metadata: Metadata = {
	title: "Community — Yapitalism",
	robots: { index: false },
};

const CA = "FhfKgR6Mcm95UUyWe7iNgWEa45nRjBwMkaVEisSRpump";

export default function CommunityPage() {
	return (
		<main className="communityWrap">
			<p className="eyebrow">community</p>
			<h1>Things the community made.</h1>
			<p>
				A community-created token exists on pump.fun. <b>We did not create
				it, do not endorse it, and cannot vouch for it.</b> It is not
				required for anything, it unlocks nothing, and the product will
				never check whether you hold it. Treat it like anything else you
				did not verify yourself.
			</p>
			<p className="caLabel">contract address, verbatim, so you can verify rather than trust a link:</p>
			<code className="ca">{CA}</code>
			<p>
				<a href="/">← back to the product</a>
			</p>
		</main>
	);
}
