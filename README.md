# 🏃‍♂️ Pace Runner

**Pace Runner** is a lightweight, file-system-based job scheduler designed to efficiently handle **rate-limited or resource-constrained service calls** by deferring them to a recurring cron job. Never worry about hitting an API rate limit again; simply queue your jobs, and let Pace Runner manage the execution, retries, and notifications.

## ✨ Features

  * **Rate-Limit Management:** Automatically defers job execution, preventing immediate rate limit errors.
  * **Decoupled Execution:** Submit jobs immediately in your main application and process them later via a scheduled cron job.
  * **Customizable Executors:** Define your own logic for processing the job payload and handling rate limits within the executor.
  * **Pluggable Messengers:** Get real-time updates and alerts for job completions or errors via **Slack, Email, File Log,** or any other custom messenger service.
  * **Local Persistence:** Uses the local file system for robust job data storage and file locks to ensure single-instance cron execution.
  * **Job Inspection:** Easily inspect the status, error messages, and retry count of all pending jobs.
  * **Duplicate Job Skipping:** **Automatically skips the submission of duplicate jobs** based on a customizable comparison key within the job's payload.

-----

## 🛠️ Installation

```bash
pip install pace-runner
```

-----

## 🚀 Two-Step Quick Start

Using Pace Runner is a simple two-step process: **Submitting Jobs** and **Running the Executor**.

### Step 1: Submit Jobs

Use the `add_job` function in your main script whenever you need to execute a rate-limited operation.

In this step, you define the job's **payload** (the data needed for the task) and the **output path** where the result will be saved.

```python
# main_application_script.py

import random
from pace_runner import BaseJobPayload, add_job
from dataclasses import dataclass, field

# 1. Define the custom job payload
@dataclass
class TempJobPayload(BaseJobPayload):
    """
    Payload for a custom processing job.
    
    The 'compare=True' argument marks this field as part of the unique key
    used to detect and skip duplicate jobs.
    """
    # This 'id' field will be used for duplicate check
    item_id: str = field(init=True, compare=True)
    
    # This 'text' field will NOT be used for duplicate check
    text_to_process: str = field(init=True, compare=False) 

temp_path = "/tmp/job_outputs"

# 2. Add jobs to the pending queue
# The first two jobs have the same 'item_id', so the second one will be skipped.
jobs_to_submit = [
    ("A001", "Initial text for item A001"),
    ("A001", "Updated text for item A001 - THIS WILL BE SKIPPED"), # Duplicate ID
    ("A002", "Text for item A002")
]

submitted_count = 0
for item_id, text in jobs_to_submit:
    was_added = add_job(
        payload=TempJobPayload(item_id=item_id, text_to_process=text), 
        # The executor will save its JSON output to this path
        output_path=f"{temp_path}/item_{item_id}.json"
    )
    if was_added:
        submitted_count += 1

print(f"Attempted to submit {len(jobs_to_submit)} jobs.")
print(f"Submitted {submitted_count} unique jobs to the queue. They will be processed by the cron job.")
```

-----

### Step 2: Run the Executor via Cron

Create a separate Python script, `cron_job.py`, which defines the execution logic and notification method. Schedule this script to run at regular intervals (e.g., every 5 minutes) using **cron**.

#### A. Define the Executor Script (`cron_job.py`)

```python
# cron_job.py

from pace_runner import JobExecutorBase, MessengerBase, SystemJob

# 1. Define the Job Executor
# This class contains the core logic for running your time-limited service.
class TempJobExecutor(JobExecutorBase):
    """
    Executes the actual rate-limited task.
    The returned python dict will be saved as a JSON file at job.output_path.
    """
    def _execute_handler_logic(self, job: SystemJob) -> dict:
        print(f"Executing job: {job.id} with payload: {job.payload}")
        
        # --- PLACE YOUR RATE-LIMITED SERVICE CALL HERE ---
        # e.g., result = tts_service.synthesize(job.payload.text_to_process)
        
        # For this example, we'll just return the payload's data
        return {"input_id": job.payload.item_id, "processed_text": job.payload.text_to_process, "status": "completed"}

# 2. Define the Messenger
# This class sends alerts on errors and batch completion status.
class PrintMessenger(MessengerBase):
    """Sends notifications to the console."""
    # NOTE: The base JobExecutorBase's run_cron_job method controls *when* to call 
    # this method (e.g., upon error or completion of a batch).
    def _send_notification(self, message: str):
        # The message includes error alerts and cron completion messages.
        # You would implement your Slack/Email/etc. API call here.
        print(message)


# 3. Run the cron job
executor = TempJobExecutor(messenger=PrintMessenger())
# The run_cron_job method handles file locks, job retrieval, execution, 
# and determines when to send notifications via the messenger.
executor.run_cron_job()
```

#### B. Schedule with Cron

Add the following entry to your crontab to run the script every five minutes:

```bash
# To edit your crontab: crontab -e
# PACE_RUNNER_SETTINGS_TOML is defined in configuration section below

*/5 * * * * PACE_RUNNER_SETTINGS_TOML=/path/to/custom_settings.toml /usr/bin/env python3 /path/to/your/cron_job.py
```

-----

## ♻️ Duplicate Job Handling

Pace Runner automatically determines if a new job being submitted is a duplicate of a currently pending job and, if so, **will skip the submission**. This prevents resource waste on jobs that have already been queued.

### How Duplicates are Identified

A job is considered a duplicate if the "compare key" generated from its payload is identical to one already in the queue.

The compare key is generated from the fields in your `BaseJobPayload` dataclass that are explicitly marked with **`compare=True`**.

#### **Customizing the Compare Key**

You control which parts of your job data make a job unique by setting `compare=True` (for inclusion) or `compare=False` (for exclusion) on the dataclass fields.

This comparison logic is also **recursive**, meaning you can define nested objects within your main payload and control their inclusion in the unique key.

#### **Example of a Recursive Compare Key**

In this example, the unique key will only be based on `TempJobPayload.id` and `ChildTempJobPayload.child_key`. Any change to `text` or `arr` will not result in a new job being submitted if the compare fields are the same.

```python
import random
from dataclasses import dataclass, field
from pace_runner import BaseJobPayload

# 1. Define a nested payload object (does not need to inherit BaseJobPayload)
@dataclass
class ChildTempJobPayload:
    # 'child_key' is included in the unique job key (compare=True)
    child_key: str = field(
        init=False, default_factory=lambda: random.choice(["key_A", "key_B"]), compare=True
    )

# 2. Define the main job payload
@dataclass
class TempJobPayload(BaseJobPayload):
    # 'text' is NOT included in the unique job key (compare=False)
    text: str = field(init=True, compare=False)
    
    # 'arr' is NOT included in the unique job key (compare=False)
    arr: list = field(init=True, compare=False)
    
    # 'id' IS included in the unique job key (compare=True)
    id: str = field(init=False, default="hello", compare=True)
    
    # 'child' is an object whose compare=True fields will be used recursively
    child: ChildTempJobPayload = field(init=False, default_factory=ChildTempJobPayload)

# A job with id="hello", text="new", arr=[1], and child.child_key="key_A"
# will be a duplicate of a job with id="hello", text="old", arr=[2], and child.child_key="key_A".
# The fields marked 'compare=False' are safely ignored for the duplicate check.
```

-----

## 🔍 Job Management and Inspection

You can manage the pending job queue outside of the execution script using simple utility functions.

```python
# job_inspector.py

from pace_runner import get_pending_jobs, remove_all_jobs, remove_job_by_id

# 1. Inspect all pending jobs
all_jobs = get_pending_jobs()

if all_jobs:
    print(f"Found {len(all_jobs)} pending jobs.")
    
    # Analyze jobs, for example, checking for failed ones
    for job in all_jobs:
        if job.error_message:
            print(f"Job ID: {job.id} failed with error: {job.error_message}")
            bad_job_id = job.id 

    # 2. Remove a specific job by its ID (e.g., a problematic one)
    if 'bad_job_id' in locals():
        was_job_removal_succesful = remove_job_by_id(bad_job_id)
        print(f"Removal of job {bad_job_id} successful: {was_job_removal_succesful}")


# 3. Or, remove all pending jobs at once
# num_removed_jobs = remove_all_jobs()
# print(f"Removed {num_removed_jobs} jobs.")
```

-----

## ⚙️ Configuration

Pace Runner is configured using a **TOML file**, which must be located at a path exposed via the environment variable **`PACE_RUNNER_SETTINGS_TOML`**.

### Example `pace_runner_settings.toml`

The configuration controls job metadata, output paths, and logging behavior.

```toml
[default]
# Controls the timezone used for all timestamps (e.g., job submission time,
# last execution time) for consistency and accurate logging.
tz = "Asia/Kolkata"

[default.output]
# The base directory where all job-specific output JSON files will be saved.
# The final path is: base/job_name/job_id.json.
base = "/home/limited_user/Data/cron_job_outputs"

# A subdirectory name used to group jobs. Useful for distinguishing between 
# different types of services (e.g., "tts_jobs" vs "image_gen_jobs").
job_name = "default_job"

[default.logs]
# The base directory for Pace Runner's internal log files.
base = "/tmp/pace_runner_logs"

# If true, log messages will also be streamed to the console (stdout/stderr).
# Recommended to set to 'false' when running as a scheduled cron job.
enable_stream = false

# The minimum severity level for logs to be recorded (e.g., DEBUG, INFO, WARNING, ERROR).
level = "INFO"
```