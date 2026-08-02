import type { NextConfig } from "next";

const nextConfig: NextConfig = {
	reactStrictMode: true,
	// The landing page is static; no server work is needed to serve it.
	output: "export",
};

export default nextConfig;
