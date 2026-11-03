# NVMe Admin Command Set (sample)

## 5 Admin Command Set

Admin commands are submitted to the Admin Submission Queue (queue identifier 0) and complete on the Admin Completion Queue. They manage the controller, its queues, namespaces, firmware and logs. I/O commands are never submitted to the Admin Submission Queue.

### 5.1 Admin command opcodes

Figure 40: Admin command opcodes
| Opcode | Command | Notes |
|---|---|---|
| 00h | Delete I/O Submission Queue | Mandatory |
| 01h | Create I/O Submission Queue | Mandatory |
| 02h | Get Log Page | Mandatory |
| 04h | Delete I/O Completion Queue | Mandatory |
| 05h | Create I/O Completion Queue | Mandatory |
| 06h | Identify | Mandatory |
| 08h | Abort | Mandatory |
| 09h | Set Features | Mandatory |
| 0Ah | Get Features | Mandatory |
| 0Ch | Asynchronous Event Request | Mandatory |
| 0Dh | Namespace Management | Optional |
| 10h | Firmware Commit | Optional |
| 11h | Firmware Image Download | Optional |
| 14h | Device Self-test | Optional |
| 15h | Namespace Attachment | Optional |
| 18h | Keep Alive | Optional |
| 80h | Format NVM | Optional |
| 81h | Security Send | Optional |
| 82h | Security Receive | Optional |
| 84h | Sanitize | Optional |

A controller that receives an admin opcode it does not support shall complete the command with status Invalid Command Opcode.

### 5.2 Identify command

The Identify command (opcode 06h) returns a 4096-byte data buffer that describes the controller, a namespace, or a list of identifiers. The Controller or Namespace Structure (CNS) field in Command Dword 10 bits 07:00 selects which data structure is returned. The Controller Identifier (CNTID) field in Command Dword 10 bits 31:16 is used by CNS values that return controller lists.

#### 5.2.1 CNS values

Figure 41: Identify - CNS values
| CNS | Data structure returned | NSID usage |
|---|---|---|
| 00h | Identify Namespace data structure for the specified NSID | Active NSID |
| 01h | Identify Controller data structure | Ignored |
| 02h | Active Namespace ID list (up to 1024 NSIDs greater than NSID) | Starting NSID |
| 03h | Namespace Identification Descriptor list | Active NSID |
| 10h | Allocated Namespace ID list | Starting NSID |
| 11h | Identify Namespace data structure for an allocated NSID | Allocated NSID |
| 12h | Controller list of controllers attached to the NSID | Allocated NSID |
| 13h | Controller list of all controllers in the NVM subsystem | Ignored |

If the host specifies a reserved CNS value, the controller shall abort the command with status Invalid Field in Command. If CNS 00h is used with an inactive NSID, the controller returns a zero-filled data structure.

#### 5.2.2 Identify Controller data structure

Figure 42: Identify Controller - selected fields
| Bytes | Field | Description |
|---|---|---|
| 01:00 | VID | PCI Vendor ID |
| 23:04 | SN | Serial Number, 20 ASCII characters |
| 63:24 | MN | Model Number, 40 ASCII characters |
| 71:64 | FR | Firmware Revision, 8 ASCII characters |
| 77 | MDTS | Maximum Data Transfer Size, a power of two in units of the minimum memory page size; 0h means no limit |
| 79:78 | CNTLID | Controller ID |
| 257:256 | OACS | Optional Admin Command Support bitmap |
| 258 | ACL | Abort Command Limit, 0's based |
| 259 | AERL | Asynchronous Event Request Limit, 0's based |
| 260 | FRMW | Firmware Updates: number of firmware slots and whether slot 1 is read only |
| 261 | LPA | Log Page Attributes |
| 262 | ELPE | Error Log Page Entries, 0's based |
| 319 | FWUG | Firmware Update Granularity in 4 KiB units |
| 331:328 | SANICAP | Sanitize Capabilities: which sanitize actions are supported |
| 519:516 | NN | Number of Namespaces supported |
| 521:520 | ONCS | Optional NVM Command Support |
| 525 | VWC | Volatile Write Cache present |
| 531 | NWPC | Namespace Write Protection Capabilities |

The OACS field indicates support for optional admin commands: bit 1 Format NVM, bit 2 Firmware Commit and Firmware Image Download, bit 3 Namespace Management and Namespace Attachment.

#### 5.2.3 Identify Namespace data structure

Figure 43: Identify Namespace - selected fields
| Bytes | Field | Description |
|---|---|---|
| 07:00 | NSZE | Namespace Size: total number of logical blocks |
| 15:08 | NCAP | Namespace Capacity: maximum number of logical blocks that may be allocated |
| 23:16 | NUSE | Namespace Utilization: logical blocks currently allocated |
| 25 | NLBAF | Number of LBA Formats supported, 0's based |
| 26 | FLBAS | Formatted LBA Size: bits 3:0 select the active LBA format |
| 131:128 | LBAF0 | LBA Format 0 support |

Each LBA Format descriptor contains the LBA Data Size (LBADS) in bits 23:16, reported as a power of two (a value of 9 means 512 bytes, 12 means 4096 bytes), and the Metadata Size (MS) in bits 15:00. A value of LBADS smaller than 9 is not supported.

### 5.3 Get Log Page command

The Get Log Page command (opcode 02h) returns a data buffer containing the requested log page. The Log Page Identifier (LID) is in Command Dword 10 bits 07:00. The Number of Dwords (NUMD) to return is 0's based and is split into NUMDL (Command Dword 10 bits 31:16) and NUMDU (Command Dword 11 bits 15:00). The Log Page Offset (LPOL in Command Dword 12 and LPOU in Command Dword 13) lets the host read a large log in pieces; the offset is in bytes and shall be dword aligned. The Retain Asynchronous Event (RAE) bit, Command Dword 10 bit 15, controls whether reading the log clears a pending asynchronous event.

If the host requests a log page that is not supported, the controller shall abort the command with status Invalid Log Page. If the offset is beyond the size of the log page, the controller shall abort the command with status Invalid Field in Command.

#### 5.3.1 Log page identifiers

Figure 44: Log page identifiers
| LID | Log page | Scope |
|---|---|---|
| 01h | Error Information | Controller |
| 02h | SMART / Health Information | Controller or namespace |
| 03h | Firmware Slot Information | NVM subsystem |
| 04h | Changed Namespace List | Controller |
| 05h | Commands Supported and Effects | Controller |
| 06h | Device Self-test | Controller |
| 07h | Telemetry Host-Initiated | Controller |
| 08h | Telemetry Controller-Initiated | Controller |
| 81h | Sanitize Status | NVM subsystem |

### 5.4 Set Features and Get Features commands

Set Features (opcode 09h) changes the value of a controller feature and Get Features (opcode 0Ah) reads it. The Feature Identifier (FID) is in Command Dword 10 bits 07:00. For Set Features, the Save (SV) bit, Command Dword 10 bit 31, requests that the value persist across power cycles and resets; if the feature is not saveable the controller shall abort the command with status Feature Identifier Not Saveable.

For Get Features, the Select (SEL) field in Command Dword 10 bits 10:08 chooses which value is returned: 000b current, 001b default, 010b saved, 011b supported capabilities.

#### 5.4.1 Feature identifiers

Figure 45: Feature identifiers
| FID | Feature |
|---|---|
| 01h | Arbitration |
| 02h | Power Management |
| 04h | Temperature Threshold |
| 05h | Error Recovery |
| 06h | Volatile Write Cache |
| 07h | Number of Queues |
| 08h | Interrupt Coalescing |
| 0Bh | Asynchronous Event Configuration |
| 0Ch | Autonomous Power State Transition |
| 0Dh | Host Memory Buffer |
| 0Fh | Keep Alive Timer |
| 84h | Namespace Write Protection Config |

#### 5.4.2 Number of Queues feature

The Number of Queues feature (FID 07h) requests the number of I/O Submission Queues (NSQR, bits 15:00) and I/O Completion Queues (NCQR, bits 31:16), both 0's based. The controller reports the number of queues it actually allocated in Dword 0 of the completion queue entry, which may be fewer than requested. A value of FFFFh for either field is invalid and the command shall be aborted with status Invalid Field in Command.

The host should set this feature during initialization, before creating any I/O queues. If Set Features for Number of Queues is issued after any I/O Submission or I/O Completion Queue has been created, the controller shall abort the command with status Command Sequence Error.

#### 5.4.3 Temperature Threshold feature

The Temperature Threshold feature (FID 04h) sets an over temperature or under temperature threshold, in Kelvin, for the composite temperature or an individual sensor. When the composite temperature crosses a threshold, the controller sets the temperature bit in the Critical Warning field of the SMART / Health Information log and may generate an asynchronous event.

### 5.5 Format NVM command

The Format NVM command (opcode 80h) performs a low level format of a namespace or of all namespaces. Command Dword 10 contains: LBA Format (LBAF) in bits 03:00, Metadata Settings (MSET) in bit 04, Protection Information (PI) in bits 07:05, Protection Information Location (PIL) in bit 08, and Secure Erase Settings (SES) in bits 11:09.

SES values: 000b no secure erase requested, 001b User Data Erase, 010b Cryptographic Erase. If the host selects an LBA format that is not supported, the controller shall abort the command with status Invalid Format. After a format completes, the contents of user data are indeterminate unless a secure erase was requested, and all previous data shall not be retrievable after a User Data Erase.

### 5.6 Sanitize command

The Sanitize command (opcode 84h) starts a sanitize operation that alters all user data in the entire NVM subsystem so that recovery of previous data is not possible. Unlike Format NVM, sanitize is not limited to one namespace and it also clears caches, the Controller Memory Buffer and deallocated or unused storage. A sanitize operation runs in the background; the command completes once the operation has started.

#### 5.6.1 Sanitize actions

The Sanitize Action (SANACT) field is Command Dword 10 bits 02:00: 001b Exit Failure Mode, 010b Start a Block Erase sanitize operation, 011b Start an Overwrite sanitize operation, 100b Start a Crypto Erase sanitize operation. Allow Unrestricted Sanitize Exit (AUSE) is bit 03, Overwrite Pass Count (OWPASS) is bits 07:04 and No Deallocate After Sanitize (NDAS) is bit 09. The overwrite pattern is supplied in Command Dword 11.

The host determines which actions are supported from the SANICAP field of the Identify Controller data structure. Requesting an unsupported sanitize action shall result in status Invalid Field in Command.

#### 5.6.2 Behavior during sanitize

While a sanitize operation is in progress, the controller shall abort most commands, including I/O commands, with status Sanitize In Progress. Permitted commands include Get Log Page for the Sanitize Status log page, Identify, and Get Features. Progress is reported by the Sanitize Status log page (LID 81h): SPROG (bytes 01:00) is the fraction complete as a numerator of 65536, and SSTAT (bytes 03:02) bits 02:00 report the status: 000b never sanitized, 001b most recent sanitize completed successfully, 010b sanitize in progress, 011b most recent sanitize failed. A sanitize operation continues across a controller reset or power cycle until it completes.

### 5.7 Firmware Image Download and Firmware Commit commands

Firmware Image Download (opcode 11h) transfers all or part of a firmware image to the controller. The Number of Dwords (NUMD) is in Command Dword 10 and is 0's based; the dword Offset (OFST) is in Command Dword 11. Pieces should be sized and aligned to the Firmware Update Granularity (FWUG) reported in Identify Controller.

Firmware Commit (opcode 10h) commits the downloaded image to a firmware slot. Firmware Slot (FS) is Command Dword 10 bits 02:00 and Commit Action (CA) is bits 05:03.

Figure 46: Firmware Commit actions
| CA | Action |
|---|---|
| 000b | Downloaded image replaces the image in the slot; not activated |
| 001b | Downloaded image replaces the slot image and is activated at the next reset |
| 010b | The existing image in the slot is activated at the next reset |
| 011b | Downloaded image replaces the slot image and is activated immediately without a reset |

Command specific status values for Firmware Commit include Invalid Firmware Slot (06h), Invalid Firmware Image (07h) and Firmware Activation Requires Conventional Reset (0Bh). Writing to a read-only slot 1 shall fail with Invalid Firmware Slot.

### 5.8 Namespace Management and Namespace Attachment commands

Namespace Management (opcode 0Dh) creates or deletes namespaces. The Select (SEL) field in Command Dword 10 bits 03:00 is 0h for create and 1h for delete. For create, the host supplies a data structure with NSZE, NCAP, FLBAS and NMIC, and the controller returns the new NSID in Dword 0 of the completion. A newly created namespace is not attached to any controller.

Namespace Attachment (opcode 15h) attaches (SEL 0h) or detaches (SEL 1h) a namespace to or from the controllers listed in a controller list. Command specific status values include Namespace Insufficient Capacity (15h), Namespace Identifier Unavailable (16h), Namespace Already Attached (18h), Namespace Is Private (19h), Namespace Not Attached (1Ah) and Controller List Invalid (1Ch).

### 5.9 Abort command

The Abort command (opcode 08h) is a best-effort request to abort a previously submitted command. The Submission Queue Identifier (SQID) is Command Dword 10 bits 15:00 and the Command Identifier (CID) is bits 31:16. Bit 0 of Dword 0 of the completion is cleared to 0 if the command was aborted and set to 1 if it was not. The number of concurrently outstanding Abort commands is limited by ACL in Identify Controller; exceeding it shall fail with status Abort Command Limit Exceeded. A command that was aborted completes with status Command Abort Requested.

### 5.10 Asynchronous Event Request command

The Asynchronous Event Request command (opcode 0Ch) has no command-specific fields. The host keeps one or more of these outstanding; the controller completes one when an event occurs. The completion Dword 0 reports the Asynchronous Event Type in bits 02:00 (0h error status, 1h SMART / health status, 2h notice, 6h I/O command specific, 7h vendor specific), Asynchronous Event Information in bits 15:08 and the associated Log Page Identifier in bits 23:16.

The host clears the event by reading the indicated log page with the RAE bit cleared; until then, events of that type are masked. Submitting more outstanding requests than AERL allows shall fail with status Asynchronous Event Request Limit Exceeded.

### 5.11 Keep Alive command

The Keep Alive command (opcode 18h) restarts the controller's Keep Alive Timer. The timeout value (KATO) is configured with the Keep Alive Timer feature (FID 0Fh) in milliseconds. If no Keep Alive command or other admin command is received within the timeout, the controller treats the host as failed. Keep Alive is mainly used over NVMe over Fabrics transports.
