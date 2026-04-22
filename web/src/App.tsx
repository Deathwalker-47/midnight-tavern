import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { authApi } from "./api/auth";
import { ApiError } from "./api/client";
import { Layout } from "./components/Layout";
import { CharactersPage } from "./pages/CharactersPage";
import { ChatsPage } from "./pages/ChatsPage";
import { ChatPage } from "./pages/ChatPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { useAuthStore } from "./store/authStore";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore();
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const { user, setUser } = useAuthStore();
  const navigate = useNavigate();

  // Validate stored session on mount
  useEffect(() => {
    if (!user) return;
    authApi.me().then(setUser).catch((err: unknown) => {
      if (err instanceof ApiError && err.status === 401) {
        setUser(null);
        navigate("/login", { replace: true });
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Layout>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/characters" element={<RequireAuth><CharactersPage /></RequireAuth>} />
        <Route path="/chats" element={<RequireAuth><ChatsPage /></RequireAuth>} />
        <Route path="/chat/:chatId" element={<RequireAuth><ChatPage /></RequireAuth>} />
        <Route path="/" element={<Navigate to="/characters" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
