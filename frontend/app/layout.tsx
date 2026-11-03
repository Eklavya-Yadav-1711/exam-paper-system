import "./styles.css";

export const metadata = { title: "Question Paper System", description: "Department examination workflow" };

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
