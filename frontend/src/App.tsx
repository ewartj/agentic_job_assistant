import { ApplicationPage } from "./components/ApplicationPage";
import { ApplicationsPage } from "./components/ApplicationsPage";
import { Home } from "./components/Home";
import { JobsPage } from "./components/JobsPage";
import { RunPage } from "./components/RunPage";
import { APPLICATIONS, HOME, JOBS, useRoute, type Route } from "./routes";

const NAV: { href: string; label: string; pages: Route["page"][] }[] = [
  { href: HOME, label: "Home", pages: ["home", "run"] },
  { href: JOBS, label: "Saved jobs", pages: ["jobs"] },
  { href: APPLICATIONS, label: "Applications", pages: ["applications", "application"] },
];

export function App() {
  const route = useRoute();
  return (
    <main className="page">
      <nav className="nav" aria-label="Pages">
        <span className="brand">Job assistant</span>
        {NAV.map((n) => (
          <a key={n.href} href={n.href} aria-current={n.pages.includes(route.page) ? "page" : undefined}>
            {n.label}
          </a>
        ))}
      </nav>
      <Page route={route} />
    </main>
  );
}

function Page({ route }: { route: Route }) {
  switch (route.page) {
    case "run":
      return <RunPage key={route.runId} runId={route.runId} />;
    case "jobs":
      return <JobsPage />;
    case "applications":
      return <ApplicationsPage />;
    case "application":
      return <ApplicationPage key={route.applicationId} applicationId={route.applicationId} />;
    case "home":
      return <Home />;
  }
}
