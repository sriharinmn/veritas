import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "Veritas — fact knowledge layer",
  description:
    "Grounded facts from financial documents, and whether they corroborate, contradict, or reconcile through context.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main className="min-h-[calc(100vh-41px)]">{children}</main>
      </body>
    </html>
  );
}
