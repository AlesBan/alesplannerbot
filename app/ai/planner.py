from datetime import datetime

from app.database.models import Task
from app.integrations.openai_client import OpenAIClient
from app.services.scheduler import ScheduledItem


PLANNER_SYSTEM_PROMPT = """You are an intelligent productivity assistant optimizing schedule while preventing burnout.

Rules:
1) Prioritize tasks with deadlines and high priority.
2) Keep plans realistic, include short breaks.
3) Protect rest time and avoid overload.
4) Balance work and life.
5) If overloaded, suggest what to postpone.
"""


class AIPlanner:
    def __init__(self) -> None:
        self.openai = OpenAIClient()

    def explain_plan(
        self,
        now: datetime,
        tasks: list[Task],
        scheduled_items: list[ScheduledItem],
        memory: dict[str, str],
    ) -> str:
        tasks_blob = "\n".join(
            [
                f"- {task.title} ({task.duration_minutes}m, {task.priority.value}, {task.energy_cost.value}, deadline={task.deadline})"
                for task in tasks
            ]
        )
        plan_blob = "\n".join(
            [f"- {item.start.strftime('%H:%M')} - {item.end.strftime('%H:%M')}: {item.title} [{item.energy_cost.value}]" for item in scheduled_items]
        )
        memory_blob = "\n".join([f"- {k}: {v}" for k, v in memory.items()]) or "- no long-term memory"

        user_prompt = f"""Current time: {now.isoformat()}
Tasks:
{tasks_blob or "- no tasks"}

Proposed schedule:
{plan_blob or "- no tasks scheduled"}

User context memory:
{memory_blob}

Generate:
1) concise explanation of schedule quality
2) overload risk warning (if any)
3) one work-life balance recommendation
"""
        return self.openai.chat_completion(PLANNER_SYSTEM_PROMPT, user_prompt)
