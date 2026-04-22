import { Link, useNavigate } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import { authApi } from "../api/auth";

interface Props {
  children: React.ReactNode;
}

export function Layout({ children }: Props) {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();

  async function handleLogout() {
    await authApi.logout().catch(() => {});
    logout();
    navigate("/login");
  }

  return (
    <div className="flex flex-col h-full">
      <header className="flex-shrink-0 h-12 border-b border-gray-800 flex items-center px-4 gap-4">
        <Link to="/" className="font-semibold text-indigo-400 hover:text-indigo-300 transition-colors">
          🌙 Midnight Tavern
        </Link>
        <nav className="flex items-center gap-3 ml-2">
          <Link to="/characters" className="text-sm text-gray-400 hover:text-gray-200 transition-colors">
            Characters
          </Link>
          <Link to="/chats" className="text-sm text-gray-400 hover:text-gray-200 transition-colors">
            Chats
          </Link>
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {user ? (
            <>
              <span className="text-sm text-gray-400">{user.display_name ?? user.username}</span>
              <button
                onClick={handleLogout}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                Logout
              </button>
            </>
          ) : (
            <Link to="/login" className="text-sm text-indigo-400 hover:text-indigo-300">
              Sign in
            </Link>
          )}
        </div>
      </header>
      <main className="flex-1 overflow-hidden">{children}</main>
    </div>
  );
}
