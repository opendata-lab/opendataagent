/** Formatting the durable user response for model context. */
interface Answer {
  question?: string;
  header?: string;
  selected?: string[];
  other?: string;
  skipped?: boolean;
  auto?: boolean;
}

function errorResult(text: string) {
  return { content: [{ type: "text" as const, text }], details: { error: text }, isError: true };
}

/** The user's answers as the model should read them. */
export function formatAnswers(answers: Answer[]): string {
  if (!answers.length) {
    return "用户跳过了全部问题，未作答。请按你的判断继续，并在回答中说明所做的假设。";
  }
  const auto = answers.some((answer) => answer.auto);
  const lines = answers.map((answer, index) => {
    const title = answer.question || answer.header || `问题 ${index + 1}`;
    const picks = [...(answer.selected ?? [])];
    const other = String(answer.other ?? "").trim();
    const parts = [picks.length ? picks.join("、") : answer.skipped || !other ? "跳过（未作答）" : ""];
    if (other) {
      parts.push(`补充：${other}`);
    }
    return `${index + 1}. ${title} → ${parts.filter(Boolean).join("；")}`;
  });
  const header = auto ? "用户长时间未回复，已按推荐选项继续：" : "用户的回答：";
  return `${header}\n${lines.join("\n")}`;
}
