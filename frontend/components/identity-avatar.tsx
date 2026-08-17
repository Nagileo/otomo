"use client";

import { useEffect, useState } from "react";
import { UserRound } from "lucide-react";

type AvatarProps = {
  className?: string;
  username?: string;
  avatarUrl?: string;
};

export function OtomoAvatar({ className = "" }: Pick<AvatarProps, "className">) {
  return (
    <span className={`identity-avatar otomo-avatar ${className}`.trim()} aria-label="Otomo">
      <img src="/otomo-avatar.png" alt="" />
    </span>
  );
}

export function UserAvatar({ className = "", username = "", avatarUrl = "" }: AvatarProps) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [avatarUrl]);
  const initial = username.trim().slice(0, 1).toUpperCase();
  return (
    <span className={`identity-avatar user-avatar ${className}`.trim()} aria-label={username ? `@${username}` : "用户"}>
      {avatarUrl && !broken ? (
        <img src={avatarUrl} alt="" referrerPolicy="no-referrer" onError={() => setBroken(true)} />
      ) : initial ? (
        <span aria-hidden="true">{initial}</span>
      ) : (
        <UserRound size="48%" aria-hidden="true" />
      )}
    </span>
  );
}
