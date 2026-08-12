import { DesignPrototype } from "@/components/design-prototype";
import { designPreviewEnabled } from "@/lib/design-preview";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

export default function DesignPreviewPage() {
  if (!designPreviewEnabled()) notFound();
  return <DesignPrototype />;
}
