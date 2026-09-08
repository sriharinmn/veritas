import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/Nav";

/**
 * IBM Plex, not Inter.
 *
 * Plex was drawn for an engineering company and reads as institutional rather
 * than as a product — which is the right register for something a finance
 * reader is meant to check rather than enjoy. The sans and mono are one
 * superfamily, so figures set in the mono sit on the same skeleton as the prose
 * around them instead of looking pasted in.
 *
 * Both are downloaded at build time and served from this origin, so the app
 * still works with no network at all — which is the premise of the offline
 * snapshot.
 */
const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-plex-sans",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-plex-mono",
});

export const metadata: Metadata = {
  title: "Veritas — fact knowledge layer",
  description:
    "Grounded facts from financial documents, and whether they corroborate, contradict, or reconcile through context.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <Nav />
        <main>{children}</main>
      </body>
    </html>
  );
}
