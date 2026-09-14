const githubPages = process.env.MEMORYGUARD_GITHUB_PAGES === "true";
const basePath = githubPages ? "/MemoryGuard" : "";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  basePath,
  assetPrefix: basePath ? `${basePath}/` : undefined,
  reactStrictMode: true,
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
