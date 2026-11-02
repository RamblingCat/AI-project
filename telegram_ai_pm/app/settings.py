"""Initial heuristics are configurable; structure, not word count, decides scope."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningSettings:
    project_results: int = int(os.getenv('PM_PROJECT_RESULTS', '3'))
    project_workstreams: int = int(os.getenv('PM_PROJECT_WORKSTREAMS', '2'))
    max_tasks: int = int(os.getenv('PM_MAX_PLAN_TASKS', '20'))
    default_hour: int = int(os.getenv('PM_DEFAULT_DEADLINE_HOUR', '18'))
    calibration_samples: int = int(os.getenv('PM_CALIBRATION_SAMPLES', '10'))
    simple_review_minutes: int = int(os.getenv('PM_SIMPLE_REVIEW_MINUTES', '30'))
    project_effort_minutes: int = int(os.getenv('PM_PROJECT_EFFORT_MINUTES', '960'))
    project_horizon_days: int = int(os.getenv('PM_PROJECT_HORIZON_DAYS', '10'))

    def __post_init__(self):
        if (self.project_results < 2 or self.project_workstreams < 2
                or not 1 <= self.max_tasks <= 50 or not 0 <= self.default_hour <= 23
                or self.calibration_samples < 3 or self.simple_review_minutes < 1
                or self.project_effort_minutes < 1 or self.project_horizon_days < 1):
            raise ValueError('Invalid PM planning settings')


SETTINGS = PlanningSettings()
