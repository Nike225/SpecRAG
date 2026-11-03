# NVMe Log Pages (sample)

## 8 Log Pages

Log pages report controller state, errors, health and history. They are read with the Get Log Page command. Several log pages are global to the NVM subsystem, while others are specific to a controller or namespace.

### 8.1 Error Information log page

The Error Information log page (LID 01h) is a set of 64-byte entries, each describing one error. The number of entries the controller retains is ELPE plus one, where ELPE is reported in the Identify Controller data structure. The entries are returned most recent first. An entry whose Error Count is 0h is not a valid error entry.

Figure 90: Error Information log entry
| Bytes | Field | Description |
|---|---|---|
| 07:00 | Error Count | 64-bit incrementing count that uniquely identifies the error; it is never reused |
| 09:08 | SQID | Submission Queue ID of the command that failed, or FFFFh if not specific to a command |
| 11:10 | CID | Command ID of the command that failed |
| 13:12 | Status Field | Status Field of the failed command (bit 0 is the Phase Tag) |
| 15:14 | Parameter Error Location | Byte (bits 07:00) and bit (bits 10:08) in the command that caused the error |
| 23:16 | LBA | First LBA that experienced the error, if applicable |
| 27:24 | Namespace | NSID of the namespace associated with the error |
| 28 | Vendor Specific Information Available | If non-zero, the log page ID of additional vendor information |
| 29 | Transport Type | Transport type of the error |
| 39:32 | Command Specific Information | Additional information for the command |

When the Parameter Error Location points at a field that caused Invalid Field in Command, validation software can confirm the controller identified the exact offending field.

### 8.2 SMART / Health Information log page

The SMART / Health Information log page (LID 02h) is 512 bytes and reports health over the life of the controller. Counts are retained across power cycles unless noted.

Figure 91: SMART / Health Information log page - selected fields
| Bytes | Field | Description |
|---|---|---|
| 0 | Critical Warning | Bitmap of critical conditions, see below |
| 02:01 | Composite Temperature | Current composite temperature in Kelvin |
| 3 | Available Spare | Normalized percentage (0 to 100) of remaining spare capacity |
| 4 | Available Spare Threshold | When Available Spare falls below this, a critical warning is raised |
| 5 | Percentage Used | Vendor estimate of life used; may exceed 100, capped at 255 |
| 47:32 | Data Units Read | Number of 512-byte data units read, reported in thousands (1 = 1000 units), rounded up |
| 63:48 | Data Units Written | Number of 512-byte data units written, reported in thousands, rounded up |
| 79:64 | Host Read Commands | Number of read commands completed |
| 95:80 | Host Write Commands | Number of write commands completed |
| 111:96 | Controller Busy Time | Minutes the controller was busy with I/O |
| 127:112 | Power Cycles | Number of power cycles |
| 143:128 | Power On Hours | Hours powered on, excluding non-operational power states |
| 159:144 | Unsafe Shutdowns | Times power was lost without a shutdown notification |
| 175:160 | Media and Data Integrity Errors | Unrecovered data integrity errors |
| 191:176 | Number of Error Information Log Entries | Error entries over the controller's life |
| 195:192 | Warning Composite Temperature Time | Minutes above the warning temperature threshold |
| 199:196 | Critical Composite Temperature Time | Minutes above the critical temperature threshold |

#### 8.2.1 Critical Warning bits

Critical Warning bit 0 is set when Available Spare has fallen below Available Spare Threshold. Bit 1 is set when a temperature is above an over temperature threshold or below an under temperature threshold. Bit 2 indicates that NVM subsystem reliability has been degraded by significant media errors or an internal error. Bit 3 indicates that all media has been placed in read only mode. Bit 4 indicates that the volatile memory backup device has failed. A controller may report a critical warning as an asynchronous event of type SMART / health status, if enabled by the Asynchronous Event Configuration feature.

### 8.3 Firmware Slot Information log page

The Firmware Slot Information log page (LID 03h) reports the firmware revision held in each slot. The Active Firmware Info (AFI) byte reports the currently active slot in bits 02:00 and, in bits 06:04, the slot that will be activated at the next reset. Firmware Revision for Slot 1 (FRS1) through Slot 7 (FRS7) are 8-byte ASCII strings; an unused slot is reported as zero.

### 8.4 Commands Supported and Effects log page

The Commands Supported and Effects log page (LID 05h) has one 4-byte entry per admin opcode and one per I/O opcode. Each entry reports whether the command is supported (CSUPP) and its side effects: whether it may change logical block content (LBCC), namespace capabilities (NCC), the namespace inventory (NIC) or controller capabilities (CCC), and the scope at which commands must be quiesced while it runs. Host software uses this log to decide whether to rescan namespaces after a vendor specific command.
