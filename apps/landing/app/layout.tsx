import type { Metadata } from "next";

import "./globals.css";

const publicAssetPath =
  process.env.MEMORYGUARD_GITHUB_PAGES === "true" ? "/MemoryGuard" : "";

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.MEMORYGUARD_GITHUB_PAGES === "true"
      ? "https://atharvmantri.github.io/MemoryGuard"
      : "https://memoryguard.atharv.me",
  ),
  title: {
    default: "MemoryGuard",
    template: "%s | MemoryGuard",
  },
  description:
    "Keep your project's agent context files accurate, reviewed, and safe. MemoryGuard is a local-first, open-source alpha.",
  icons: {
    icon: [
      { url: `${publicAssetPath}/MemoryGuard_Favicon.ico`, sizes: "any" },
      {
        url: `${publicAssetPath}/MemoryGuard_Icon_32.png`,
        type: "image/png",
        sizes: "32x32",
      },
    ],
    apple: [
      {
        url: `${publicAssetPath}/MemoryGuard_Icon_256.png`,
        sizes: "256x256",
      },
    ],
  },
  openGraph: {
    title: "MemoryGuard — Stop re-teaching your codebase to AI.",
    description:
      "MemoryGuard keeps your project's agent context files accurate across sessions. Open-source alpha, runs locally from source.",
    url: "https://memoryguard.atharv.me",
    siteName: "MemoryGuard",
    type: "website",
  },
};

export const viewport = {
  themeColor: "#22323F",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
