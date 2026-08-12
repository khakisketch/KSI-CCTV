import { LoginForm } from "@/components/login-form";
import { getAuthStatus } from "@/lib/auth";

export const dynamic = "force-dynamic";

export default async function LoginPage() {
  const { mode } = await getAuthStatus();
  return <LoginForm mode={mode} />;
}
