#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/* INA219 / INA226 Register Map */
#define INA219_REG_CONFIG           0x00
#define INA219_REG_SHUNTVOLTAGE     0x01
#define INA219_REG_BUSVOLTAGE       0x02
#define INA219_REG_POWER            0x03
#define INA219_REG_CURRENT          0x04
#define INA219_REG_CALIBRATION      0x05  /* Note: Recalculated per INA226 datasheet §8.6 */

/* INA226 Specific Registers */
#define INA226_REG_MASK_ENABLE      0x06
#define INA226_REG_ALERT_LIMIT      0x07

/* Configuration register bit fields */
/* INA219_CONFIG_BADCRES_12BIT has been removed since INA226 uses 16-bit fixed resolution */

#endif /* HAL_INA219_H */