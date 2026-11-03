# NVMe Queues, Completion and Status (sample)

## 3 Queue Model and Command Processing

NVMe uses paired submission and completion queues in host memory. The host places 64-byte commands in a Submission Queue (SQ) and the controller places 16-byte completion entries in the associated Completion Queue (CQ). Multiple submission queues may share one completion queue.

### 3.1 Queue creation and deletion

The Admin Submission and Completion Queues are configured through controller registers (AQA, ASQ and ACQ) before the controller is enabled. I/O queues are created with admin commands. The host shall create an I/O Completion Queue (Create I/O Completion Queue, opcode 05h) before creating any I/O Submission Queue that uses it (Create I/O Submission Queue, opcode 01h). The maximum queue size is CAP.MQES plus one entries.

Command specific errors for queue creation: Completion Queue Invalid (00h) when the SQ names a CQ that does not exist; Invalid Queue Identifier (01h) when the queue ID is 0, already in use, or above the number of queues allocated by the Number of Queues feature; Invalid Queue Size (02h) when the size is 0 or exceeds CAP.MQES. Deleting a completion queue while submission queues still use it shall fail with Invalid Queue Deletion (0Ch). When an I/O Submission Queue is deleted, outstanding commands in it complete with status Command Aborted due to SQ Deletion.

### 3.2 Doorbell registers

Each queue has a doorbell register. The host writes the new tail pointer to the Submission Queue y Tail Doorbell to notify the controller of new commands, and writes the new head pointer to the Completion Queue y Head Doorbell to release completion entries it has consumed. The SQ y tail doorbell is at offset 1000h + (2y * (4 << CAP.DSTRD)) and the CQ y head doorbell is at 1000h + ((2y + 1) * (4 << CAP.DSTRD)), where DSTRD is the doorbell stride. Writing an invalid doorbell value is reported as an asynchronous error event rather than a command status.

### 3.3 Phase Tag

The Phase Tag (P) bit lets the host detect new completion entries without reading a register. The controller inverts the phase tag value it writes each time it wraps around the completion queue. Completion queue memory is initialised to zero, so on the first pass new entries have P set to 1. The host processes entries while the phase tag matches its expected value and flips its expected value at each wrap.

### 3.4 Completion queue entry

Figure 20: Completion queue entry layout
| Dword | Bits | Field |
|---|---|---|
| 0 | 31:00 | Command Specific result |
| 1 | 31:00 | Reserved |
| 2 | 15:00 | SQ Head Pointer (SQHD) |
| 2 | 31:16 | SQ Identifier (SQID) |
| 3 | 15:00 | Command Identifier (CID) |
| 3 | 16 | Phase Tag (P) |
| 3 | 31:17 | Status Field (SF) |

The Status Field contains: Status Code (SC) in bits 07:00, Status Code Type (SCT) in bits 10:08, Command Retry Delay (CRD) in bits 12:11, More (M) in bit 13 and Do Not Retry (DNR) in bit 14. When DNR is set, the host should not resubmit the same command because it is expected to fail again. SQHD tells the host how far the controller has consumed the submission queue, so the host can reuse those slots.

### 3.5 Status Code Types

Figure 21: Status Code Type values
| SCT | Meaning |
|---|---|
| 0h | Generic Command Status |
| 1h | Command Specific Status |
| 2h | Media and Data Integrity Errors |
| 3h | Path Related Status |
| 7h | Vendor Specific |

### 3.6 Generic command status values

Figure 22: Generic command status values (SCT 0h)
| SC | Status |
|---|---|
| 00h | Successful Completion |
| 01h | Invalid Command Opcode |
| 02h | Invalid Field in Command |
| 03h | Command ID Conflict |
| 04h | Data Transfer Error |
| 05h | Commands Aborted due to Power Loss Notification |
| 06h | Internal Error |
| 07h | Command Abort Requested |
| 08h | Command Aborted due to SQ Deletion |
| 0Bh | Invalid Namespace or Format |
| 0Ch | Command Sequence Error |
| 1Dh | Sanitize In Progress |
| 20h | Namespace is Write Protected |
| 80h | LBA Out of Range |
| 81h | Capacity Exceeded |
| 82h | Namespace Not Ready |

Command ID Conflict is returned when the host submits a command whose CID is already in use on the same submission queue. Invalid Namespace or Format is returned when the NSID is not valid for the command or the namespace is not formatted for the requested operation.

### 3.7 Media and Data Integrity Errors

Figure 23: Media and Data Integrity Error values (SCT 2h)
| SC | Status |
|---|---|
| 80h | Write Fault |
| 81h | Unrecovered Read Error |
| 82h | End-to-end Guard Check Error |
| 83h | End-to-end Application Tag Check Error |
| 84h | End-to-end Reference Tag Check Error |
| 85h | Compare Failure |
| 86h | Access Denied |
| 87h | Deallocated or Unwritten Logical Block |

## 4 NVM I/O Commands

I/O commands operate on a namespace identified by the NSID field and are submitted to I/O Submission Queues.

### 4.1 I/O command opcodes

Figure 30: NVM I/O command opcodes
| Opcode | Command |
|---|---|
| 00h | Flush |
| 01h | Write |
| 02h | Read |
| 04h | Write Uncorrectable |
| 05h | Compare |
| 08h | Write Zeroes |
| 09h | Dataset Management |
| 0Ch | Verify |

### 4.2 Read and Write commands

For Read and Write, the Starting LBA (SLBA) is in Command Dwords 10 and 11. Command Dword 12 holds the Number of Logical Blocks (NLB) in bits 15:00, which is 0's based, the Protection Information field (PRINFO) in bits 29:26, Force Unit Access (FUA) in bit 30 and Limited Retry (LR) in bit 31. When FUA is set on a write, the controller shall commit the data to non-volatile media before completing the command. If SLBA plus NLB exceeds the namespace size, the command shall fail with LBA Out of Range. A transfer larger than MDTS shall fail with Invalid Field in Command.

### 4.3 Flush command

The Flush command (opcode 00h) commits data and metadata in the volatile write cache to non-volatile media. If no volatile write cache is present, Flush completes successfully with no effect. An NSID of FFFFFFFFh applies the flush to all namespaces attached to the controller.

### 4.4 Dataset Management command

Dataset Management (opcode 09h) gives the controller hints about ranges of logical blocks. The Number of Ranges (NR) in Command Dword 10 bits 07:00 is 0's based, allowing up to 256 ranges, each described by a 16-byte range entry. When the Attribute - Deallocate (AD) bit, Command Dword 11 bit 02, is set, the ranges are deallocated (the operation commonly called trim). Reads of deallocated blocks return data according to the namespace's deallocate read behavior.
