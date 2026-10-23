export function Banner({ text }: { text?: string }) {
  return (
    <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-950">
      {text || "合成演示数据。模拟实验成功不代表现实世界有效。本系统不能作为临床或公共卫生决策指令。"}
    </div>
  );
}
