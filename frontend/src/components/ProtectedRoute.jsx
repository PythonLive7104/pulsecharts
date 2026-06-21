// Gate routes behind authentication. Anonymous visitors are sent to /login,
// remembering where they were headed so we can return them after sign-in.
import { Navigate, useLocation } from "react-router-dom";
import { useStore } from "../store/useStore";

export default function ProtectedRoute({ children }) {
  const isAuthed = useStore((s) => s.isAuthed);
  const location = useLocation();

  if (!isAuthed) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}
