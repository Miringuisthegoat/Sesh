"""Wording for print-job statuses. Truthful: 'sent to printer' means Windows accepted the job, nothing more."""
JOB_LABELS = {"QUEUED": "Waiting", "CLAIMED": "Picked up", "DOWNLOADING": "Fetching file", "DOWNLOADED": "Ready to send",
              "SUBMITTED_TO_PRINTER": "Sent to printer", "COMPLETED": "Printed", "FAILED": "Failed", "CANCELLED": "Cancelled",
              "UNKNOWN": "Check printer"}
JOB_HELP = {
    "QUEUED": "Waiting for the office computer to pick this job up. It can only print while that computer is on and connected.",
    "CLAIMED": "The office computer has picked up the job.",
    "DOWNLOADING": "The office computer is fetching the PDF.",
    "DOWNLOADED": "The office computer has the PDF and is about to send it to the printer. It can no longer be cancelled.",
    "SUBMITTED_TO_PRINTER": "Windows on the office computer accepted the job into its print queue. This does not prove that paper has come out. Check the printer.",
    "COMPLETED": "An administrator or a reliable printer signal confirmed that this job printed.",
    "FAILED": "This job did not print.",
    "CANCELLED": "This job was cancelled before it was sent to the printer.",
    "UNKNOWN": "It is not certain whether this job printed (for example the office computer restarted at the wrong moment). It will NOT be printed again automatically. An administrator needs to check the printer.",
}
