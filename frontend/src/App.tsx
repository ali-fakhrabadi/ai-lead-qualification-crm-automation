import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router";
import { api, hasSession, setSignedOutHandler, type Me, type Role } from "./api";
import { Shell } from "./components/Shell";
import { AuditPage } from "./pages/AuditPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DuplicatesPage } from "./pages/DuplicatesPage";
import { FollowUpsPage } from "./pages/FollowUpsPage";
import { LeadDetailPage } from "./pages/LeadDetailPage";
import { LeadFormPage } from "./pages/LeadFormPage";
import { LeadsPage } from "./pages/LeadsPage";
import { LoginPage } from "./pages/LoginPage";
import { WorkflowPage } from "./pages/WorkflowPage";

interface Session {
  me: Me | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  can: (role: Role) => boolean;
}

const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(hasSession());
  const navigate = useNavigate();

  useEffect(() => {
    setSignedOutHandler(() => {
      setMe(null);
      navigate("/login");
    });
    if (hasSession()) {
      api
        .me()
        .then(setMe)
        .catch(() => setMe(null))
        .finally(() => setLoading(false));
    }
  }, [navigate]);

  const signIn = useCallback(async (email: string, password: string) => {
    await api.login(email, password);
    setMe(await api.me());
  }, []);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setMe(null);
    navigate("/login");
  }, [navigate]);

  const can = useCallback((role: Role) => (me ? RANK[me.role] >= RANK[role] : false), [me]);

  return (
    <SessionContext.Provider value={{ me, loading, signIn, signOut, can }}>
      {children}
    </SessionContext.Provider>
  );
}

function Protected({ children }: { children: ReactNode }) {
  const { me, loading } = useSession();
  if (loading) {
    return (
      <div className="center-screen" role="status">
        Loading…
      </div>
    );
  }
  return me ? <Shell>{children}</Shell> : <Navigate to="/login" replace />;
}

const PROTECTED: [string, ReactNode][] = [
  ["/", <DashboardPage key="dashboard" />],
  ["/leads", <LeadsPage key="leads" />],
  ["/leads/:leadId", <LeadDetailPage key="lead" />],
  ["/duplicates", <DuplicatesPage key="duplicates" />],
  ["/follow-ups", <FollowUpsPage key="follow-ups" />],
  ["/workflow", <WorkflowPage key="workflow" />],
  ["/audit", <AuditPage key="audit" />],
];

export function App() {
  return (
    <SessionProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/demo-form" element={<LeadFormPage />} />
        {PROTECTED.map(([path, element]) => (
          <Route key={path} path={path} element={<Protected>{element}</Protected>} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </SessionProvider>
  );
}
